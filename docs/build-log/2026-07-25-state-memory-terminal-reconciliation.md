# State-aware memory terminal reconciliation

## Outcome

The local state-aware memory branch is complete as an economical engineering
decision, not as a SOTA result.

- Clean tri-domain assembly: **proven** at `235e4531`, with the complete public
  gate recorded in `2026-07-25-state-memory-clean-assembly.md`.
- State-aware Deep workspace: **proven as a mechanism**. Exact current compiled
  units are rendered before raw sources, deterministically and downstream of
  tenant/context/query-policy filtering. Raw evidence and receipt validation
  remain intact. Deep egress now omits tenant, subject, scope, agent, actor,
  internal source-reference, contextual-chunk, and scheduling metadata.
- STALE paired pilot: **rejected** as
  `REJECTED_STOP_NO_BROADENING`. It produced no official score and supports no
  SOTA claim.
- LongMemEval-V2 paired pilot: **rejected before paid execution**. The current
  adapter cannot construct structured state from its resource jobs; the obvious
  extension is incompatible with the extractor input bound and is not an
  economical pilot. The official full matrix is therefore deferred and
  forbidden under this run's gate.
- Private Syndai mirror parity: **externally blocked** by the separately dirty
  private `STATUS.md`; this branch does not edit the private checkout.

## Benchmark inventory

### STALE

- Code: `ea7d391103a151927cd29d2f01d87597a782bdcb`, Apache-2.0.
- Dataset: `617c51dc200b5ab09970834144c7e51c77959af0`, 400 scenarios,
  305,908,212 bytes, SHA-256
  `5f3ec375179e20e2e94469e018189188f34e2e7e5f21cbecbd99fcfa648c1876`.
- Development selection: declared/frozen answer-blind two Type-I plus two
  Type-II scenarios, all three probes, 12 queries. No independent selector
  algorithm/seed proof was added, so the stronger claim “proven answer-blind”
  is not made.
- Current arm: 12/12 reader answers, 12/12 Deep recalls ended `InvalidOutput`,
  25/25 returned evidence items had verified receipts. Receipts establish
  provenance, not answer correctness.
- Candidate arm: stopped during four structured attempts; three paid responses
  failed decoding and one HTTP 400 had no provider response ID, usage, or
  authoritative price. It completed zero recalls and zero reader calls.
- Cost: $0.4245304 known settled ($0.2931619 reader plus $0.1313685 extractor),
  plus unresolved current-Deep and HTTP-400 liabilities. Native judging and the
  400-scenario expansion did not run.
- Canonical closures:
  `AUTHORIZATION-1-CLOSURE.json` and `AUTHORIZATION-2-CLOSURE.json` hash-bind the
  original immutable authorization packets. Both packets are closed/consumed;
  their embedded historical commands are not reusable authorization.

### LongMemEval-V2

- Code: `be15ea6e995462f3391c1a610892df3f67dfa7bd`, Apache-2.0.
- Dataset: `f152293e235517d504809563c833d7190b8c713b`, Apache-2.0,
  7,120,369,667 bytes. Verification passed 37 upstream checksum entries, the
  one exact locked README exception, and six separately locked files.
- Official protocol: 451 questions, 200K memory context, reader
  `Qwen/Qwen3.5-9B`, judge `gpt-5.2` medium.
- Development selection: frozen exposed n=12 balanced across web/enterprise and
  static state, dynamic state, workflow, gotchas, and premise awareness.
- Free feasibility: 5,979 trajectory uses, 180,019 states, 4,223,862,197
  canonical bytes, and 7,934 resource jobs. The adapter queues
  `ReflectResource`, while canonical structured extraction accepts
  `ReflectEpisode`; the candidate arm would therefore be a no-op. A naive
  resource extension has 5,931 resource uses above the 128-KiB provider request
  limit and a $5,557.100544 three-attempt maximum-token construction liability.
- Decision: `REJECTED_PREPAID_FEASIBILITY_NO_RUN`; no reader, Deep, judge, or
  other paid LongMemEval call ran, and there is no pilot accuracy or SOTA result.
- Machine proof: `artifacts/state-memory-sota/longmemeval-v2-pilot/FEASIBILITY.json`.

## Research-backed next technique

The official LongMemEval-V2 work evaluates memory construction, updating,
retrieval, and generation together; STALE separately tests state resolution,
premise resistance, and policy adaptation. The evidence here points to the
construction boundary, not another retrieval engine. The next authorizable
design is an incremental, content-addressed tool/resource compiler over bounded
contextual chunks, followed by deterministic operation folding into the same
bitemporal store. Repeated prompt/model/input hashes may reuse extraction
outputs, but every tenant receives fresh canonical mutations, lineage, and
receipts. This is an inference from the official benchmark contracts plus the
measured 1,338-unique/5,979-used trajectory overlap; it has not been implemented
or benchmarked.

Primary sources: LongMemEval-V2 repository and paper
(`https://github.com/xiaowu0162/LongMemEval-V2`,
`https://arxiv.org/abs/2605.12493`); STALE repository and paper
(`https://github.com/icedreamc/STALE`, `https://arxiv.org/abs/2605.06527`).

## Local dependency order

1. Reviewed 65-commit tri-domain assembly from current `origin/main` through
   `5dda4f0f`, preserving migrations `001 -> 002 -> 003`.
2. `235e4531` clean-assembly proof.
3. `9ff5ab9f` current compiled state in Deep and STALE disposition propagation.
4. `d6987d5c`, `88c35225` structured provider output/request liability bounds.
5. `17b20229`, `a3baa487` immutable LME verification and native judge cap.
6. `db24306c` through `04fb276b` STALE authorization, operational fix, closure,
   and rejection evidence; `5d52c0c7` was explicitly reverted by `700090ea`.
7. `5bb4a3bd` reverts the ineffective LME environment-only change.
8. `5185ef91` minimizes compiled-state Deep egress and repairs the shared store
   contract.
9. `3688dd32` decouples sealed historical evidence validation from mutable live
   inputs while retaining live pre-execution prompt drift rejection.

No push, merge, deployment, production traffic, or production database write
occurred. Fast remains deterministic/default; Deep remains explicit and
accuracy-first. No overall or benchmark-specific SOTA claim is supported.

## Verification

The final gate ran at code head `3688dd32` with only this reconciliation,
STATUS note, and machine feasibility proof uncommitted:

- Python: 853 passed, 0 failed, 12 skipped.
- `cargo fmt --check`: passed.
- `cargo clippy --all-targets --all-features -- -D warnings`: passed.
- Rust all-targets/all-features: 701 passed, 0 failed, 92 ignored. The ignored
  set consists of explicit live-model/network and scratch-Postgres opt-ins.
- Rust doc tests: 0 tests, 0 failures, 0 ignored.
- Scratch Postgres/worker opt-ins excluding the SLO test: 76 passed, 0 failed.
- Fast Postgres hot-path SLO: failed twice on candidate (p50 301.198 ms and
  229.013 ms against 200 ms). The detached baseline also failed on the same
  host at 202.348 ms. This is reported as a separate environment-sensitive red
  predicate, not attributed to the Deep-only change and not called green.
- Provider lint: plain-postgres, Supabase, and Neon passed.
- Migration dry run: passed in order `20260703_001`, `20260723_002`,
  `20260724_003`.
- Real-binary/scratch-Postgres E2E: all checks passed, including MCP projection,
  cross-tenant denial, restart durability, correction, and forget/no-resurrection.
- Public/private spec drift: dirty only for `STATUS.md`; the private mirror is a
  separately owned checkout and was not modified.

The worktree contained no unrelated changes. Benchmark-derived reader caches
and lock/checkpoint leftovers were preserved outside Git at
`/Users/sidsharma/.cache/memphant-bench/state-memory-sota-stale-run2-uncommitted/`.
