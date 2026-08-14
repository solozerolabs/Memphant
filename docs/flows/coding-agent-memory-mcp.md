# Coding-agent memory MCP and Syndai integration

## Spec

### Outcome

Deliver the smallest evidence-backed MemPhant product slice that a coding agent
can connect to through MCP and that Syndai coding/CaaS can call without owning a
second memory system:

1. a reviewed procedural experience can be stored with provenance and explicit
   lifecycle evidence in MemPhant's existing PostgreSQL authority;
2. a coding agent can retrieve at most one relevant, cited card through the real
   MCP path when it reaches an actionable work boundary;
3. the host can record shown/activated/helpful/harmful outcomes through the
   existing outcome substrate;
4. candidate review, correction, supersession, and forgetting remain human- and
   host-governed;
5. Codex and Claude can use the same public MCP contract, and Syndai adds only
   host orchestration.

The first natural-task control/treatment screen is a targeted traversal and
actionability diagnostic. A positive result may open the next engineering task;
it cannot confirm general actionability, authorize a capability promotion, or
support a coding-improvement/SOTA claim. A negative result can still kill a dead
mechanism cheaply.

### Product boundary

- PostgreSQL remains the only durable authority. Supabase, Neon, and plain
  PostgreSQL are deployment profiles, not different semantics.
- Markdown is an inspectable projection. It is never a competing truth store.
- Git, `rg`, LSP, and current repository files remain authoritative for current
  code. MemPhant stores prior decisions, procedures, traps, corrections, and
  source pointers that the current tree cannot reconstruct.
- Host runtimes own checkpoints and short-lived working state.
- Raw coding transcripts remain in their existing host retention system. A
  future MemPhant candidate may cite a scoped source checksum/pointer; MemPhant
  must not duplicate raw transcripts.
- No external graph/vector database, code index, KB service, mobile surface,
  dashboard, automatic promoter, or startup memory dump is in this slice.

### Lifecycle truth

- Human review may authorize a candidate for an isolated experiment; it does
  not make the procedure `Validated`.
- `Validated` requires persisted replay or deterministic proof, including the
  safety/high-risk assertion required by the memory-model spec.
- The coding agent never receives a validation or promotion mutation verb.
- Automatic promotion remains closed. Outcome rows may inform later review but
  cannot promote a procedure by themselves.
- A card version/hash, originating source/task identity, proof, and later-task
  identity must remain distinguishable. Source and later task must differ for
  any reuse claim.

### Experience card v1

Persist one bounded, versioned `ProcedurePayload` in the existing memory-unit
JSONB payload. The exact Rust names may follow current local conventions, but
the logical content is:

- schema version;
- trigger;
- applicable phase (`ANALYZE`, `REPRODUCE`, `EDIT`, or `VERIFY`) as card data,
  not a new public recall enum;
- invariant/action;
- negative or focused check;
- source references and source-task identity;
- card content hash;
- optional review authorization;
- optional deterministic validation proof. Replay is a later schema evolution
  only after its acceptance predicate is defined.

The human-readable body remains the compact agent-facing rendering. Structured
data supports filtering, lifecycle checks, citations, and future evolution. Do
not add columns or a new table when the existing JSONB and lineage tables hold.

### Recall UX

- The host supplies phase and task intent in query/trace context initially.
- The agent-facing MCP input is exactly `{query}`. Identity, mode, eligibility,
  budgets, and ranking defaults are server-owned.
- A response returns zero or one card by default for coding work, with source,
  trust, age/freshness, lifecycle state, and verification guidance.
- The agent is told that memory may be stale or wrong and must verify current
  repository facts with native tools.
- Empty and unavailable are observably different. Runtime delivery may fail
  open to no augmentation; review and lifecycle writes fail closed and loudly.
- The wire result is one of three states: `hit` is one cited item plus trace;
  `empty` is success with no items, abstention, and trace; `unavailable` is a
  retryable typed tool error. Authentication/scope failures are typed,
  non-retryable, and never serialized as empty.
- C0 and M1 experiments use identical MCP configuration, instructions, tool
  overhead, repository state, model, reasoning effort, native tools, and host
  augmentations. C0 binds an empty isolated scope; M1 binds one frozen card.

### BDD acceptance

Given an isolated tenant/scope and a procedure candidate with source provenance,
when a host submits valid deterministic proof, then the same
card content becomes recall-eligible in one lineage-linked `Validated`
generation, the Candidate remains visible to historical reads, the proof is
inspectable, and no agent-facing tool can perform that transition.

Given a validated card whose phase/trigger match the work boundary, when Codex
or Claude calls MemPhant recall through MCP, then the response contains at most
one cited card and an inspectable trace; when no eligible card exists, recall
returns an honest empty result.

Given a card shown to a later task, when the host records activation and the
terminal outcome, then the existing attribution path accepts only tenant-bound,
shown units and preserves idempotency, correction, and forgetting semantics.

Given an unserved Candidate and one later deterministic task proof, when a
service-key host explicitly invokes validation, then proof validation is
independent of later recall utility: no helpful/activated event is required to
validate the procedure, and no task outcome can validate it automatically.

Given identical C0 and M1 natural-task arms, when both agents complete the task,
then a memory win requires a material decision or defect improvement, no
treatment-only material defect, and evidence that the card arrived before the
decision. Retrieval score or a polished explanation alone is flat.

### Kill/default-off rules

- No recall call in the voluntary-pull screen: agentic pull is default-off;
  permit one host-triggered boundary screen.
- Card arrives late, is ignored, duplicates current-tree facts, or causes
  unverified action: kill that card/source lane.
- Empty and raw-episode treatments are flat: keep raw coding ingestion off.
- Startup delivery ties boundary delivery: keep the simpler startup/agentic
  path and do not add a phase adapter.
- Lexical and hybrid return the same bytes: keep the cheaper PostgreSQL
  exact/lexical path; no model calls and no alternate engine.
- A source lane ties native `rg`/git/Context7: leave that lane off.
- Fewer than two independent source lanes survive: do not build automated
  nomination/capture.
- A measured PostgreSQL SLO must fail before any external graph/vector store is
  reconsidered.

## Plan

### Global constraints

- Work only in isolated linked worktrees. Preserve dirty primary checkouts.
- Read all callers before changing shared types or lifecycle behavior.
- Use TDD for production behavior: one failing behavioral test, then the
  smallest root-cause implementation.
- Reuse existing memory units, JSONB payload, lineage, trace, review/FSRS,
  task-outcome, task-memory-event, scope policy, and MCP surfaces.
- Generated OpenAPI/MCP artifacts are regenerated from binaries, never edited.
- No backwards-compatibility shim is required in this pre-production repo.
- No new experiment-validator framework. Natural tasks use their existing
  focused acceptance/checks plus blind trajectory/diff review.
- Paid work uses scratch PostgreSQL and the explicitly authorized Syndai
  Doppler development configuration only around the secret-consuming command.
  Never wrap the coding-agent process in the full Syndai environment.
- The budget is a ceiling: Codex Terra high for discovery; Claude Opus medium
  only for final portability. Stop below USD 30 or 16 task calls. If execution
  has no settled USD cost, track calls, tokens, and wall time.
- Before every paid arm, reserve its worst-case cost from the pinned model
  price and maximum input/output tokens in the existing budget ledger. Deny
  dispatch when the reservation could cross the remaining USD ceiling; settle
  actual cost afterward. Subscription/no-settlement runs enforce the call cap
  and token caps instead of inventing a dollar value.
- Budget 16 calls as: up to four liveness/representation arms, two timing arms,
  eight source-lane arms, and one final two-arm portability screen. A
  host-triggered rescue or lexical/hybrid pair displaces the lowest-priority
  unopened source pair; the final portability pair stays reserved and the
  ceiling never increases.

### Task 0: Baseline and freeze the first screen

1. Run the existing MemPhant MCP target tests and the historical outcome-flow
   focused test in this clean worktree. Record baseline failures without fixing
   unrelated work.
2. Select one public-safe, genuinely later Syndai task and one older source from
   `LEARNINGS.md`, an ADR, or an outcome record. Exclude `AGENTS.md`, current-tree
   facts, held-out symbols, patches, and answer leakage.
3. Freeze base SHA, task, model/CLI versions, prompt wrapper, native tools,
   resumption policy, existing Syndai augmentation hashes/flags, source/card
   hashes, and existing task-specific checks.
4. Render the same evidence once as a compact card and once as a bounded raw
   episode. Do not ingest a corpus or add distractors.

Expected finding: one high-signal, source-older-than-task procedure exists that
cannot be recovered as cheaply from the current tree. Kill the entire wedge if
none exists.

### Task 1: Make MCP recall principal-derived and prove the real process

1. Replace the MCP-facing recall input with exactly `{query}`. Require a fully
   context-bound API key and derive subject, generation, actor, scope, and agent
   node from the existing bound principal. Keep REST `RecallHttpRequest`
   unchanged and do not add a second coding-recall verb. Hardcode cost-first
   defaults: exact/lexical fast recall, limit one, beliefs/deep/provider calls
   off, and bounded response size.
2. Regenerate the MCP tool artifact and prove that identity fields cannot be
   supplied by the model. Revalidate key existence, revocation, and complete
   binding on every recall; a long-running stdio process must not retain access
   after revocation. Preserve traces, citations, abstention, and safe errors.
3. Define and test the three-state wire contract (`hit`, `empty`, `unavailable`)
   plus stable non-retryable auth/scope failures with corrective detail. Cover
   missing, tenant-only, partially bound, wrong-context, revoked-after-startup,
   and backend-unavailable keys.
4. Reuse the scratch-PostgreSQL wrapper and real MCP binary. Create a
   source-linked unit through the normal episode/worker path, then use a
   test-only transaction to change that exact unit to procedural/validated.
   This fixture is inaccessible to agents and is not product promotion.
5. Extend the real-process MCP probe to call `tools/call recall`. C0 must return
   empty; M1 must return exactly the expected body,
   `inclusion_reason=validated_procedure`, a verified citation, and a trace
   containing the unit ID.

Expected finding: the same agent-visible schema and request bytes traverse a
real scratch PostgreSQL process. The two isolated scopes differ only in bound
credential value and contents. No reader model or lifecycle product is needed.

### Task 2: Run the first real coding-agent liveness pair

1. Connect Codex to the same built stdio server in both arms with the same
   command, environment-variable names, `required=true`, and an experiment-only
   `recall` tool allowlist. Only the context-bound API-key value and isolated
   scope contents differ.
2. Give both arms the same one-time instruction to consult available memory
   when useful and verify it against current code/docs. Do not inject the card
   into M1's prompt.
3. Before task execution, run a secret-safe preflight: client connection/list,
   recall-only discovery, expected C0 empty receipt, and M1 exact hit receipt.
   Disconnected, unavailable, wrong-tool, or stale-key state fails the run
   rather than becoming a control result. Record cleanup commands and revoke
   both scoped keys after stopping their stdio processes.
4. Run one fresh Codex Terra-high C0/M1 natural-task pair. Use JSON trajectory
   events to establish that recall preceded the material decision. Judge actual
   diffs and existing focused checks; record privacy-safe hashes, counts,
   timings, tool use, and the decision boundary, not raw prompts/transcripts.
5. Exercise the existing outcome path instead of inventing an experiment
   ledger: record `shown` only for an M1 hit, `activated` only when the
   trajectory demonstrates use, and a terminal task outcome for both arms.
   Keep the public artifact `decisional: false`; one pair is a traversal and
   actionability diagnostic, not promotion evidence.
6. Treat no call, late/error recall, asymmetric call shape, irrelevant recall,
   a tie, or a treatment-only defect as a failed voluntary-pull screen. Permit
   exactly one fresh host-triggered boundary rescue only after no voluntary
   call; do not relabel that rescue as timing causality.

Expected finding: M1 reaches the agent before the decision and changes one
material action with no new defect. If not, stop production lifecycle work and
report the failed mechanism honestly.

### Task 3: Identify representation and timing one axis at a time

Only after Task 2 survives:

1. Representation: bank Task 2's C0 at the same frozen base/task and add one
   fresh-agent arm containing identical evidence as bounded raw prose. Keep
   unit kind, lifecycle state, provenance, retrieval metadata, query, and
   delivery identical so only agent-facing bytes differ. Add a fourth
   contextual-chunks-OFF arm only if the raw rendering is plausibly useful.
   Do not globally toggle chat chunks from a coding result.
2. Timing: use a fresh matched task to compare the same winning card at
   startup versus the first real actionable boundary. Put phase in query/trace
   text; do not add a public phase enum.
3. A tie keeps the simpler behavior. A raw loss keeps raw coding capture off.

Expected finding: compact card is more actionable/token-efficient; boundary
delivery matters only for longer tasks. Maximum four additional task calls;
stop earlier on a kill.

### Task 4: Persist the minimum governed procedure lifecycle

Only after an actionable card survives:

1. Add one authoritative strict `UnitPayloadV1 { schema_version: 1,
   contextual_chunks, procedure? }` envelope. `ProcedurePayloadV1` includes
   phase/trigger, typed steps, signal kind, source episode/resource references,
   required originating task ID, validation claim, and a canonical
   server-derived card hash. One new migration rewrites all existing JSONB
   payloads, adds the deterministic-proof outcome field described below, and
   widens the mutation verb constraint; then use one strict reader with no
   dual-shape shim.
2. Fix the root state bug: every procedural retain mints `Candidate`, regardless
   of caller trust. Other memory-kind state behavior stays unchanged. Replace
   procedural body-substring signal/safety inference with typed signal/steps.
3. Add one shared `ScopeMemoryFilter { kind, state }` through
   server/service/store and apply it before cursor/limit in PostgreSQL and
   in-memory stores. Normal actor-bound inventory excludes candidates;
   service-key candidate inventory shows full source, scope, trust, age, body,
   and proof state. Authorize candidate state before accepting the filter. Add
   no dashboard or MCP candidate resource.
4. Extend the existing terminal task outcome with an optional strict
   `DeterministicProofV1` JSONB envelope. It names the candidate/card hash,
   checker ID/version, checked content hash, result, trials/wins,
   checksum-bound proof-artifact reference, and high-risk safety assertion when
   required. Only the service-key host may attach it; normal outcomes omit it.
   On outcome ingestion, resolve the named Candidate and verify its context and
   canonical card hash before persisting the proof. Unknown fields and partial
   proof fail closed.
5. Add REST-only, service-key `POST /v1/procedures/{unit_id}/validate` with an
   idempotency key. The caller supplies only candidate ID, validation task ID,
   and expected card hash. Version 1 supports stored deterministic proof only;
   replay remains deferred until its threshold/win predicate is frozen.
6. In one serializable transaction, load the candidate and terminal task
   outcome and derive the V1 predicate from the persisted proof envelope:
   correct tenant/context/hash, validation task after candidate creation,
   distinct origin and validation tasks, terminal success, validator passed,
   typed-step safety, and the required high-risk assertion. Do not require
   `shown`, `activated`, `helpful`, or recall exposure: a Candidate is not
   recallable, so those predicates create a circular lifecycle.
7. Preserve transaction-time history: close the Candidate generation and mint
   one Validated successor with lineage and an immutable proof receipt
   atomically. The receipt stores checker ID/version, candidate/card/task/content
   hashes, result, trials/wins, safety assertion, and proof-artifact checksum.
   Reuse the mutation ledger with a new `procedure_validate` verb; the single
   Task 4 migration updates its constraint, `MutationVerb`, the embedded
   migration manifest/revision, provider lint, payload rows, and task-outcome
   proof storage. Never edit an applied migration or add a second proof ledger.
8. Reject approval-only validation, actor-key access, caller-asserted proof,
   missing/failed stored proof,
   duplicate/stale/wrong-context proof, origin-task self-validation, and
   non-idempotent/concurrent retries. Human invocation authorizes proof
   evaluation; proof creates `Validated`. Later shown/activated/helpful/harmful
   events measure utility and affect review/ranking only; they never validate.
9. Keep MCP free of lifecycle promotion verbs. Ensure correction,
   supersession, exact-selector forgetting, and canonical Markdown projection
   preserve lifecycle truth. Correcting a procedural card always mints a new
   `Candidate`, clears the prior validation receipt, and recomputes the
   canonical card hash; it never clones `Validated` proof onto changed bytes.
   Before/after
   `transaction_as_of`, idempotent restart replay, and concurrent validation
   must observe exactly one successor.

Expected finding: the clean root fix fits existing JSONB/state/outcome/mutation
substrates. If it requires a second ledger or dashboard, redesign smaller.

### Task 5: Make MCP a portable coding-agent surface

1. Use existing `limit=1`, fast recall, procedural eligibility, and query/trace
   context. Do not add a coding-recall mode or phase enum.
2. Render compact trust/freshness/source/verification guidance only where the
   current structured response does not already supply it, and cite the
   underlying unit/resource. Preserve empty vs unavailable errors.
3. Publish one canonical zero-to-useful flow: start PostgreSQL/server/worker;
   create tenant; mint a tenant-only bootstrap key; submit a complete context
   binding; mint a fully bound runtime key; revoke the bootstrap key; configure
   MCP; observe honest empty; retain/review/validate one procedure; observe one
   cited hit. Measure connected-to-empty separately from first-useful-recall.
4. Add exact secret-free coding configs. Codex uses a trusted project config,
   absolute binary path, forwarded environment-variable names,
   `required=true`, and `enabled_tools=["recall"]`. Claude uses local scope by
   default (project config only with environment placeholders), explicit
   connection preflight, and recall-only permission. Plaintext keys never enter
   committed config. Operator/host mutation configuration stays separate.
5. Document preflight and cleanup with existing health, actual empty recall,
   `codex mcp list/get/remove`, `claude mcp list/get/remove`, process stop, key
   revocation, config removal, compose shutdown, and revoked-key rejection. Do
   not add a doctor command unless those native checks leave an observed state
   ambiguous.
6. Make this the only authoritative coding-agent quickstart, link it from the
   README/general quickstart, and replace or retire the nonexistent command in
   the old onboarding spec.
7. Require USD 0 provider cost per default recall. Report response bytes, cold
   build/startup, warm backend/MCP p50/p95, end-to-end Codex/Claude tool latency,
   time-to-honest-empty, and time-to-first-useful-recall separately.

Expected finding: portable configuration is host-only; no agent-specific fork or
new service is needed.

### Task 6: Close public host-SDK gaps only if the chosen host uses them

1. First confirm that Syndai or another selected product host consumes the
   public Python SDK for this path. If it calls HTTP through its existing
   adapter, defer this task rather than creating unused parity work.
2. If the SDK is a real consumer, bring it to parity only for the used product
   path: access policies, task outcomes, task-memory events, required
   idempotency/mutation headers, and the host-only procedure validation
   operation if Task 4 adds it.
3. Generate rather than hand-edit OpenAPI/MCP contracts.
4. Add request-shape, tenant-binding, idempotency, and redaction tests only for
   changed behavior. Do not introduce a second async client abstraction.

Expected finding: no new SDK abstraction is needed; extend the current client
pattern.

### Task 7: Integrate Syndai coding/CaaS in its own clean worktree

1. Base a `codex/` linked worktree on authoritative `Syndai/main`; never modify
   the dirty/stale primary checkout. Record the remote commit before work.
2. Add one first-class read-only `coding.read.memphant_procedure_recall`
   capability behind the existing `mcp__coding__run_skill` bridge shared by
   Claude and Codex. Reuse its server-side secret/egress proxy; do not create
   per-arm `CustomTool` rows whose UUIDs would alter prompt bytes.
3. Derive user/repository/agent binding server-side, call the same MemPhant
   service, and return at most one compact procedural card plus trace ID, unit
   ID, citation, and abstention. Preserve the same typed `hit | empty |
   unavailable` envelope and never collapse errors into empty. Do not
   auto-inject it.
4. Make ANALYZE/REPRODUCE/EDIT/VERIFY intent an integration-owned query/trace
   value. Deliver zero or one card at the proven timing boundary.
5. Disable `MEMPHANT_REPO_PROFILE_ENABLED` and equalize owner-approved decision
   injection for C0/M1; fresh/resume behavior and all augmentation hashes must
   be identical.
6. Add the missing MemPhant task-memory-event/task-outcome writer using the
   coding run ID as idempotent task identity. Structured response records shown;
   shown is recorded only for a hit, activation remains separate, and terminal
   outcome is always recorded.
7. Runtime recall may fail open to no augmentation in production, but a
   missed/late/error recall fails the liveness experiment. Event/review writes
   spool and fail visibly under the existing privacy contract. Do not create a
   new code KB, raw transcript copy, decision-extractor path, or mobile work.

Expected finding: Syndai owns orchestration only, while MemPhant owns durable
memory/governance. If integration requires duplicated storage, fix the adapter
boundary instead.

### Task 8: Screen retrieval/storage and source lanes economically

After stable delivery:

1. Replay the real stage queries over the same small corpus through existing
   exact/lexical, pgvector/hybrid, relational-edge, and Markdown-`rg` paths.
   Compare returned bytes/citations, abstention, latency, and tokens at USD 0.
2. If bytes are identical, select PostgreSQL exact/lexical. Run one natural-task
   lexical-vs-hybrid pair only if hybrid changes the relevant delivered unit;
   it displaces one unopened source pair and never expands the call budget.
3. Screen independently, in this order:
   - reviewed procedural lesson from prior outcome/learning;
   - semantic ADR or cross-environment fact unavailable in current tree;
   - compact repository-history intent with a Git pointer that the agent must
     verify using `git show`;
   - version-pinned official resource versus equal Context7 access;
   - direct explicit user correction (never inferred preference).
4. Judge each lane even if another fails. Keep semantic/docs/history/preferences
   off on ties or when native authority is equal/better.
5. Qualify every lane at USD 0 first. A paid pair is permitted only when a
   qualified survivor poses a unique unresolved actionability question and a
   reserved unopened pair remains; otherwise the source comparison ends with
   byte/citation/native-authority review. Do not manufacture a retrieval contest
   while the eligible corpus contains only one card.
6. Belief receives a USD 0 redundancy review. Working state stays host-owned.
   Bitemporality is lifecycle governance, not a competing memory kind. Outcomes
   remain evidence, not agent-facing content.

Expected finding: procedural is the primary served unit; at most one or two
other lanes survive. A separate graph/vector service remains unnecessary. The
four non-procedural paid screens consume at most eight task calls; USD 0
qualification may eliminate any of them first.

### Task 9: Conditional capture and ranking work

Only if at least two independent source lanes survive and manual authoring is
the measured bottleneck:

1. Nominate minimized candidates from existing host-owned source
   pointer/checksum and outcome evidence. Never post full coding sessions to
   MemPhant.
2. Human review authorizes; deterministic evidence validates; no automatic
   promotion.
3. Compare Cognee-style used-item EWMA against existing MemPhant
   outcome/FSRS ranking only in silent shadow and only if current ranking makes a
   measured selection error. Keep the existing policy otherwise.

This task is default-deferred; it is not implementation scope unless its gates
open during this flow.

### Task 10: Final cumulative UX and claim audit

1. On one genuinely new multi-stage task, run one empty-scope/cumulative pair
   on Claude Opus medium, with zero or one card per phase and no startup dump.
   This is the two-call portability replication after Codex discovery, not a
   third treatment-only call.
2. Blind-review unlabeled trajectories/diffs for requested end state, existing
   focused checks, treatment-only defects, unnecessary work, native-source
   verification, source usage/rejection, calls/tokens/time, and operator setup.
3. If cumulative memory is flat/harmful, default the affected lane off; do not
   add pairs to rescue a claim.
4. Update `STATUS.md`, evidence artifacts, docs, provider contracts, and concise
   `AGENTS.md` guidance only for capabilities proven in this flow. Preserve all
   negative and claim-boundary statements.
5. Run the full harness and obtain independent code, plan, DX, and adversarial
   review before completion.

Expected finding: one portable, governed procedural-card loop has acceptable
UX. The final statement remains scoped to the tested tasks/models/sources.

## Harness

Every applicable line must exit 0 before feature-flow verification. Conditional
Syndai commands are finalized in its linked-worktree plan after current-main
inspection.

```sh
python3 -m pytest tests/ -q
python3 -m pytest tests/test_outcome_coupled_evolution.py -q
python3 -m pytest bindings/python/tests -q
python3 scripts/check_evidence_contract.py --report
python3 scripts/check_evidence_contract.py
python3 scripts/instrument_power.py --check
python3 scripts/check_spec_drift.py
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
cargo test --doc
cargo run -p memphant-cli -- db lint --provider plain-postgres
cargo run -p memphant-cli -- db lint --provider supabase
cargo run -p memphant-cli -- db lint --provider neon
python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant MEMPHANT_TEST_DATABASE_URL cargo test -p memphant-store-postgres -p memphant-worker --all-targets -- --ignored --test-threads=1
DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant bash scripts/e2e_probe.sh
```

Live PostgreSQL tests and the real-process MCP probe use the existing scratch
database behavior. Paid/model screens are evidence steps with explicit
authorization and privacy-safe artifacts; they are not substituted by the
deterministic repository harness. Every decisional experiment artifact carries
the repository evidence contract, is registered, refreshes the retrofit report,
and passes the checker without turning the experiment into a validator campaign.
The first 1v1 and other exploratory single-pair artifacts are explicitly
`decisional: false`; they cannot flip `STATUS.md` or support promotion.

## GSTACK REVIEW REPORT

Engineering, DX, test/experiment, OSS, and adversarial reviews were integrated.
The plan separates claim, representation, timing, retrieval, source, lifecycle,
and integration variables. It reuses one authority and existing causal ledgers.
Product implementation is gated on the cheapest real MCP liveness screen, and
speculative capture/ranking/storage work is default deferred.

Resolved architecture decisions:

- MCP recall derives identity and defaults from the fully bound API-key
  principal; the model supplies exactly `{query}`. Per-call revalidation makes
  revocation effective for a running stdio server.
- The liveness fixture uses a source-linked scratch unit and a test-only state
  mutation; it does not invent product promotion.
- Procedure lifecycle uses existing JSONB, bitemporal state, task evidence, and
  mutation receipt substrates. Validation closes Candidate and mints one
  lineage-linked Validated successor with an immutable proof receipt; it does
  not depend on impossible pre-validation recall exposure. Version 1 validates
  deterministic proof only; replay is deferred until its predicate is defined.
- Syndai uses one shared read-only capability behind its existing Claude/Codex
  bridge, based on `Syndai/main`; it does not create agent-specific adapters or
  a code KB.

Review decisions:

- Accepted: bootstrap-to-bound-key onboarding, query-only MCP input, typed
  hit/empty/unavailable states, per-call auth revalidation, append-only
  validation, strict one-time payload migration, pre-pagination lifecycle
  filtering, operator/agent capability separation, USD 0 source qualification,
  explicit non-decisional 1v1 artifacts, correction-to-Candidate lifecycle,
  mandatory origin-task identity, single-variable representation bytes, and
  per-arm worst-case spend reservation.
- Rejected as premature: a new `doctor` command while existing health plus real
  empty recall and client list/get can identify the state; a separate graph or
  vector service; a second proof ledger; candidate serving solely to satisfy
  validation; SDK work without a real SDK consumer; and automatic capture or
  ranking feedback before measured demand.
- Retained intentionally: one final Claude 1v1 portability smoke because the
  user requested cross-agent proof and it fits the fixed 16-call ceiling. It is
  not a general-efficacy test. All requested storage/memory/source categories
  receive USD 0 inspection, but paid calls occur only for a unique surviving
  uncertainty.
- The prior broad paired/validator campaign is not extended. Existing tests
  remain regression evidence; natural-task comparisons use their focused checks
  and blind material-outcome review. Therefore no single-pair result promotes a
  capability or weakens the evidence contract.

NO UNRESOLVED DECISIONS
