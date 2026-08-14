# Handoff: MemPhant as the memory suite for coding agents

Current STATUS mirror: RUNTIME COMPLETE — BENCHMARK EVIDENCE PENDING

**Date:** 2026-08-13

**Audience:** the next product, research, and implementation owner

**Status:** product direction and execution handoff; no capability or benchmark claim
**Live state ledger:** [`STATUS.md`](../superpowers/specs/memphant/STATUS.md)

## 1. Executive decision

MemPhant's first complete use case should be a **governed experience layer for
coding agents**. It should let an agent carry useful knowledge across chats,
documents, repositories, tool runs, and coding tasks while preserving source,
scope, time, correction, deletion, and outcome evidence.

The product is not a replacement for the coding agent, its context window, or
native repository search. Its job is to answer a narrower question:

> What durable experience from earlier work should this agent use now, why is
> it trustworthy, and did using it improve the later task?

The first product loop is:

```text
chat / docs / KB / prior coding runs
                 |
                 v
      retain sources with scope and provenance
                 |
                 v
     compile governed facts and experience cards
                 |
                 v
   recall at ANALYZE / REPRODUCE / EDIT / VERIFY
                 |
                 v
        agent acts using native coding tools
                 |
                 v
      record exposure, use, outcome, correction
                 |
                 +----> reinforce, supersede, or forget
```

This is one product over one canonical store. “All storage substrates” means
all **logical memory planes and agent-native access forms**, not one database
per memory type:

- PostgreSQL is the authority for records, scopes, time, lineage, outcomes,
  lexical search, and vector search.
- The Markdown file plane is an editable projection for agents and humans.
- MCP, REST, CLI, the Python SDK, and the Anthropic memory-tool adapter are
  distribution surfaces over the same service.
- Plain PostgreSQL, Supabase, and Neon are deployment profiles, not different
  product semantics.
- A separate graph or vector database stays retired until a measured Postgres
  bottleneck proves it necessary.

## 2. The product boundary

### What MemPhant owns

- Durable capture of conversations, documents, coding episodes, and outcomes.
- Typed memory with provenance, trust, valid time, transaction time, scope,
  correction, supersession, and deletion.
- Budgeted, cited recall that may return nothing when evidence is weak.
- Experience reuse across agents, sessions, repositories, and projects where
  policy explicitly permits it.
- Evidence linking a shown memory to a later task result.
- A portable public contract shared by hosted and self-hosted deployments.

### What stays with the coding agent and its host

- Planning, tool use, edits, testing, and final decisions.
- Live repository exploration through `rg`, language servers, git, and code
  intelligence.
- The task's short-lived scratchpad until working-state memory earns a product
  contract.
- Model selection, sandboxing, permissions, budgets, and orchestration.
- Product-specific hooks and tenant wiring in the integrating application.

This boundary matters because MemPhant has already measured that an agent with
native repository search beats MemPhant on repo-recoverable facts. MemPhant
should provide history, decisions, procedures, failure traps, and source
pointers that repository search cannot reconstruct—not compete to locate the
current definition of a symbol.

## 3. Current state, without aspiration mixed in

### Built and reusable now

| Area | Current capability |
|---|---|
| Runtime | Public Rust server and worker backed by PostgreSQL 17 and the `memphant` schema. |
| Public surfaces | REST, MCP over stdio/HTTP, CLI, Python SDK, and generated OpenAPI/tool schemas share `MemoryService`. |
| Agent tools | `retain`, `recall`, `reflect`, `correct`, `forget`, `trace`, and `mark`. |
| Memory kinds | Episodic, semantic, procedural, belief, resource, and preference. |
| Governance | Tenant/context binding, source trust, exact scope policy, citations, append-only lineage, valid/transaction time, correction, supersession, and exact-selector forgetting. |
| Resources | Documents, code, conversations, and other resources can be retained and compiled into contextual chunks. |
| File-native access | `compile`, dry-run/apply `sync`, `verify`, MCP resources, and the Anthropic client-side memory-tool projection. PostgreSQL remains canonical. |
| Outcome evidence | Task-outcome and task-memory-event endpoints record terminal state plus shown, activated, helpful, harmful, and silenced events. |
| Delivery reliability | A local JSONL spool has real-task evidence for after-accept restart, idempotent replay, endpoint acceptance, and full drain without raw prompt/transcript persistence. |
| Deployment | Provider lint covers plain PostgreSQL, Supabase, and Neon. |

The generated public contracts are the authority for request shapes:
[`memphant.v1.json`](../../openapi/memphant.v1.json) and
[`memphant.tools.v1.json`](../../mcp/memphant.tools.v1.json).

### Measured results that constrain the product

| Finding | Product consequence |
|---|---|
| Native `grep`/`rg` beat the MemPhant path by 37.78 percentage points on repo-recoverable facts. | Keep current-code discovery native. Memory should return history, hypotheses, traps, and prior outcomes. |
| The current docs gate lost hit@10 by 13.3 points and answer accuracy by 16.7 points. | Do not call the current resource path a competitive KB product yet. Fix lifecycle and measure answer use, not only retrieval. |
| Evolving-preference recall remained below full context after the subject-resolution fix. | Do not claim general user-memory superiority or spend more on the exposed bank. |
| Forgetting scored 244/259 versus 133/259 for the baseline, with no measured regressions. | Correction and forgetting are a real differentiator worth preserving across every new surface. |
| Always-on rule injection was essentially flat on a short-task benchmark (+0.9 points), and the proposed tool veto was too imprecise. | Do not build a giant startup prompt or blocking policy engine. Retrieve small evidence when it can act. |
| A randomized ten-pair dogfood test changed violation-free completion from 0/10 to 10/10 while task success was 10/10 in both arms. | Outcome-backed delivery can enforce one scoped verification lesson. It does not prove broader coding improvement. |
| The latest exploratory one-pair small-model run changed implementation choices but left both arms with material defects. | Whole-task top-10 injection is not enough. It is an unregistered diagnostic, not a product claim. |

The last two public artifacts are
[`randomized-dogfood.json`](../build-log/artifacts/outcome-coupled-evolution/randomized-dogfood.json)
and
[`silent-shadow-real-tasks.json`](../build-log/artifacts/outcome-coupled-evolution/silent-shadow-real-tasks.json).
Their claim boundaries must remain intact.

### Designed or partially present, but not a shipped product capability

| Gap | Exact state |
|---|---|
| Automatic local capture | [`memphant capture`](../superpowers/specs/2026-08-09-memphant-capture-design.md) is a reviewed design. There is no capture module in the CLI today. |
| Hosted chat/coding capture | Integration seams are identified, but product-specific hook wiring stays outside this public repo until generalized. |
| Stage-aware coding recall | Research supports it, but the public recall request has no typed task phase and no production adapter re-queries at coding-stage boundaries. |
| Governed experience cards | Procedural recall exists, but raw trajectories are not yet compiled into the compact trigger/invariant/action/test form described below. |
| Validated procedure promotion | The read path can require `Validated`; no general write path currently promotes procedures into that state. |
| Outcome-driven promotion | The outcome ledger and experiments exist; an automatic candidate-to-active policy remains closed pending causal evidence. |
| Resource lifecycle | Resource writes lack revision deduplication, valid-time evolution, and supersession of stale generations. This blocks an honest versioned docs/KB product. |
| Working state | `working_state` is proposed but unminted. The agent host should retain task scratch state for now. |
| Multi-agent product UX | Scope and agent-node policy exist, but a polished repo/team setup and inspection flow is not yet a product. |

Do not flip a `STATUS.md` checkbox for this handoff. It records direction, not
proof that any gap is closed.

## 4. The axes MemPhant must cover

“Memory” is too broad to be one quality number. The coding-agent product is a
matrix with five independent axes.

### Axis A: logical memory plane

| Plane | Coding-agent example | MemPhant representation | State |
|---|---|---|---|
| Episodes | A chat, tool failure, implementation attempt, review, or terminal coding run | `Episodic` unit/source episode | Built |
| Project knowledge | Architecture decisions, environment facts, ownership, durable conventions | `Semantic`, `Belief`, or `Preference` | Built; quality varies |
| Docs and KB | Versioned official docs, runbooks, ADRs, internal guides | `Resource` plus derived resource units | Basic path built; lifecycle gap open |
| Procedures and experience | “When this symptom appears, inspect X, preserve Y, and prove Z” | `Procedural` experience card | Read path built; compiler/promotion open |
| Working state | Current plan, unresolved hypotheses, temporary handoff state | Proposed `working_state` | Unbuilt; host-owned |
| Outcome evidence | Task success, violations, shown/used/helpful/harmful memory | Task outcome and task-memory-event ledgers | Built |

### Axis B: source substrate

- **Chat:** direct user corrections, decisions, clarifications, and prior agent
  turns.
- **Docs/KB:** official documentation, project docs, runbooks, and knowledge
  articles with version and provenance.
- **Coding:** earlier attempts, commits, diffs, test failures, review findings,
  and environment behavior.
- **Repository history:** issue/commit associations and co-change history that
  cannot be recovered from the current tree alone.
- **Outcomes:** validators, independent review, user acceptance, regression,
  and memory exposure.

All sources normalize into the existing governed model; they do not get
separate truth stores.

### Axis C: coding phase

Memory is useful at the point where it can change an action:

| Phase | Useful memory | Bad default |
|---|---|---|
| ANALYZE | Prior issue shapes, architecture decisions, likely owners, historical hotspots | Dumping old patches before the agent understands the task |
| REPRODUCE | Environment profile, known harness traps, minimal repro patterns, prior failed approaches | Generic repo summaries |
| EDIT | Invariants, analogous changes, API/version guidance, generated-source boundaries | Ten whole-session narratives with no actionable step |
| VERIFY | Required focused/adjacent gates, migration/restart/concurrency checks, known false-positive validators | A rule shown only after the edit is complete |

The initial implementation should express the phase in the recall query and
trace metadata owned by the integration. Do not freeze a new public `task_phase`
enum until the stage-aware experiment shows value and the vocabulary survives
real agents.

### Axis D: scope and time

The minimum useful hierarchy is user → project/repository → branch/task, with
agent identity recorded separately. Cross-repo toolchain experience belongs at
the user or organization root and is inherited only through explicit policy.
Repository-specific procedures stay repo-scoped. Short-lived task state never
crosses the agent node.

Every durable item needs observed time. Mutable facts and docs also need valid
time and supersession. Transaction time remains the audit history. “Latest
vector hit” is not an acceptable replacement for these rules.

### Axis E: evidence of utility

Retrieval precision, similarity, and model explanations are diagnostics. The
product outcomes are:

1. **Task success:** the requested end state and appropriate runnable checks
   pass.
2. **Accepted without violation:** task success plus no scoped rule or quality
   violation.
3. **No adjacent regression:** independent review and the relevant neighboring
   suites find no treatment-only damage.
4. **Useful interaction:** relevant memory is shown at a stage where it can act,
   and the agent's edit/test path provides attributable evidence that it used
   or rejected it.
5. **UX rail:** added time, tokens, calls, and operator work remain reasonable.

No single benchmark score may hide a failed axis.

## 5. First use case: the experienced repository teammate

The first user is a coding agent completing a new, multi-step change in a
repository it has worked in before. The user should feel that the agent remembers
the team's hard-won experience without being buried under old transcripts.

### Inputs

- Existing `AGENTS.md`, `LEARNINGS.md`, runbooks, ADRs, and official docs.
- Earlier chat corrections and design decisions.
- Prior coding attempts, test failures, review findings, and accepted changes.
- Terminal task outcomes and memory-exposure events.
- Current repository history as pointers and summaries, while current source
  stays accessible through native tools.

### Agent experience

1. The host binds a user, repository scope, and agent node once.
2. Both control and treatment agents receive the same memory tools and the same
   instruction to consult them when useful. An empty control scope equalizes
   tool availability without exposing treatment content.
3. At ANALYZE, REPRODUCE, EDIT, and VERIFY, the agent can issue a fresh recall
   using its current task, hypothesis, symbols, error, or diff summary.
4. MemPhant returns **zero to three** cited experience cards. It abstains instead
   of filling a quota.
5. The agent uses normal repository tools to confirm the card against current
   code and docs. A card is guidance, never authority over the live tree.
6. The integration records which units were shown and activated. After external
   validation and independent review, it records the terminal outcome.
7. A user can inspect provenance, correct a bad card, or forget its source. A
   later policy may reinforce or suppress cards only from causal evidence.

### Experience-card shape

Do not add a new table for the first slice. Encode this compact form in existing
procedural-unit bodies and metadata, then promote a schema only after real use
shows which fields need queries or constraints.

```yaml
trigger: symptom, task shape, or environment condition
phase: analyze | reproduce | edit | verify
invariant: condition the change must preserve
action: smallest useful next action
negative_test: check that catches the historical failure
refs: source episode, resource, commit, or task-outcome citations
scope: repository or explicitly inherited toolchain scope
freshness: observed and valid-time information
evidence: shown, activated, helpful, harmful, and task outcomes
```

This is deliberately smaller than a transcript or a generic summary. Recent
software-agent research points in the same direction:

- [Structurally Aligned Subtask-Level Memory](https://arxiv.org/abs/2602.21611)
  reports a mean +4.7 percentage-point Pass@1 gain over vanilla agents and
  diagnoses whole-instance memory as a granularity mismatch.
- [SWE-Exp](https://arxiv.org/abs/2507.23361) separates comprehension,
  localization, and modification experience instead of storing one monolithic
  task narrative.
- [MemGovern](https://arxiv.org/abs/2601.06789) converts fragmented human
  experience into governed cards and reports a +4.65-point resolution gain on
  SWE-bench Verified.
- [Repository Memory](https://arxiv.org/abs/2510.01003) uses issue/commit
  episodes plus functional summaries and improves code localization Acc@5 by
  4.9 points over its agent baseline.

These results motivate the design. They do not validate MemPhant's
implementation or private task distribution.

## 6. Public tool suite for this use case

No new agent-facing verb is needed for the first vertical slice.

| Surface | Role in the coding loop |
|---|---|
| `retain` | Store a chat/coding episode, versioned resource, or reviewed direct unit. |
| `reflect` | Compile pending episodes/resources into typed governed units. |
| `recall` | Retrieve a small cited pack for the current coding phase. |
| `trace` | Explain candidates, suppression, packing, citations, and time. |
| `mark` | Record which recalled units the caller used or ignored. |
| `correct` | Supersede or invalidate a wrong memory with an auditable correction. |
| `forget` | Remove a selected unit, episode, or resource without later resurrection. |
| Task-outcome endpoint | Record terminal task/validator state and shown/activated IDs automatically. |
| Task-memory-event endpoint | Record shown, activated, helpful, harmful, or silenced evidence automatically. |
| File plane | Let agents and humans inspect/edit governed projections through familiar Markdown. |

The coding host, not the model, owns task lifecycle writes. An agent must not be
able to declare its own work successful or its favorite memory helpful.

## 7. Ordered build work

### 7.1 Prove the interaction before adding substrate

Build one integration-level vertical slice using only existing public
contracts:

- Import a reviewed corpus of about 100 project learnings into a scratch
  repository scope. Include roughly ten genuinely applicable cards and many
  plausible distractors.
- Keep current repository files and official docs available equally to both
  arms.
- Give both arms the same MCP tools and lifecycle. The control store is empty;
  the treatment store holds the frozen corpus.
- Ask the agent to recall again when its task understanding, failure, diff, or
  verification phase changes. Do not inject ten records once at session start.
- Capture recall traces, shown/activated IDs, edits, test attempts, terminal
  outcome, cost, and independent review without storing raw content publicly.

This step requires adapter code and task packets, not new database tables,
retrievers, or UI.

### 7.2 Build the experience compiler only if the interaction helps

If the vertical slice changes the task positively:

- Normalize successful and failed trajectories into the card shape above.
- Start with deterministic/manual nomination and existing `Candidate` state.
- Require citations and reject high-risk, secret-like, or instruction-injection
  content at the trust boundary.
- Promote only after a distinct later same-scope task had the candidate shown
  and received causal credit from an external validator, randomized comparison,
  or explicit user correction.
- Preserve failed approaches when they are useful negative experience; failure
  is evidence, not automatically harmful memory.

Do not auto-promote from frequency, model confidence, or an agent's claim that
its own advice helped.

### 7.3 Implement capture as thin transports over one normalization seam

Use the reviewed capture design rather than inventing domain-specific pipelines:

- Local coding sessions: incremental Claude Code JSONL tailing first, with
  rotation-safe cursors and partial-line recovery.
- Hosted coding runs: retain at the host's captured-stream-before-cleanup seam.
- Agentic chat: push at the existing post-turn/finalize seam because no durable
  local transcript exists.
- Docs/KB: retain resources with URI, revision, content hash, trust, and body or
  a governed pointer.
- Normalize conversations into `Turn + SessionBinding`; run privacy masking at
  the edge and nomination once on the server.

Capture stores sources and candidates. It does not imply that captured text is
safe or useful to serve.

### 7.4 Close the docs/KB lifecycle before calling it complete

The resource write path needs one authoritative revision rule:

- Same URI and content hash is idempotent.
- Same URI and new content hash creates a new valid generation.
- The prior generation becomes stale/superseded at a recorded time.
- Units derived only from the prior generation stop being current.
- Citations continue to resolve to historical content for audit.

Add this at the shared resource write path with live-Postgres tests. Do not hide
staleness in an adapter or a cleanup job.

### 7.5 Complete outcome-backed procedure state

- Add the minimum write transition needed to promote a causally validated
  procedural candidate into `Validated`.
- Require a prior shown/activated edge and a distinct terminal task outcome.
- Keep observational events for analytics only; they must not change ordering
  or lifecycle automatically.
- Apply harmful evidence by suppression or review first, not destructive
  deletion.
- Test idempotency, restart, cross-tenant isolation, missing linkage, and
  correction/forget behavior.

### 7.6 Productize scopes and agent-native inspection

- Provide one documented repository setup that binds user root, repo scope,
  and agent identity correctly.
- Keep repo/chat agents as explicitly authorized siblings; do not rely on
  accidental scope ancestry.
- Put cross-repo toolchain facts at the user/organization root only when policy
  permits inheritance.
- Make citations, lifecycle state, and correction/forget controls visible
  through the existing file/MCP surfaces before considering a dashboard.

### 7.7 Defer working-state memory and alternate stores

Keep the live task scratchpad in the agent host. Research compacted versus
host-truncated task state first. Mint `working_state` only if a paired long-task
test shows better resumption without cross-task leakage.

Keep the canonical database and current indexes until traces show a concrete
latency, recall, or graph-traversal bottleneck. The existence of graph and
vector products is not evidence that MemPhant needs another store.

## 8. Research program and why each question matters

| Question | Current hypothesis | Cheapest decisive test | Build decision |
|---|---|---|---|
| When should memory be retrieved? | Stage-aware pulls beat one startup dump because the query is grounded in the agent's current hypothesis/error/diff. | Replay frozen traces at ANALYZE/REPRODUCE/EDIT/VERIFY, then one paired live task with the same tools in both arms. | Build phase-aware adapter behavior only on better relevant-card use and no regression. |
| What is the right unit? | Compact trigger/invariant/action/negative-test cards outperform whole-session prose. | Blindly compare card versus episode retrieval on real later tasks; inspect relevance and actionability before model spend. | Keep cards only if they improve both precision and downstream use. |
| How many memories should appear? | Dynamic zero-to-three with abstention is safer than fixed top-k. | Sweep thresholds offline, then preserve the selected policy for live pairs. | Reject a policy that fills context with irrelevant cards. |
| Should recall be automatic or agentic? | The agent should query at functional stages, with a small host reminder and no treatment-only wording. | Same MCP tool in both arms; compare voluntary queries, tool-derived queries, and one host-triggered phase query. | Choose the least intrusive channel that gets relevant evidence used. |
| What belongs in memory versus native search? | Historical intent, outcomes, environment traps, and cross-repo knowledge belong in memory; current symbol/code lookup stays native. | Classify misses by whether current-tree search could recover them. | Never ingest or retrieve current code merely to duplicate `rg`/LSP. |
| How are experiences learned safely? | Outcome-linked distinct-task validation is a better gate than frequency or self-scoring. | Replay known helpful/harmful exposures and perturb missing linkage. | Automatic ordering/lifecycle fails closed without causal attribution. |
| How should docs evolve? | URI + revision + content hash + valid time is sufficient; no document graph is needed initially. | Versioned official-doc fixture with unchanged, updated, reverted, and deleted revisions. | Ship KB claims only after stale content is reliably suppressed. |
| Does working state belong in MemPhant? | Host state is sufficient for short tasks; durable compacted state may help long resumptions. | Paired compaction/resumption tasks with leakage and stale-plan checks. | Mint the kind only on task lift and zero cross-task leakage. |
| How should agents share memory? | Repo siblings need explicit grants; cross-repo toolchain memory needs deliberate root inheritance. | Two-agent/two-repo isolation and inheritance matrix. | Fail binding before retain/recall on any ambiguous policy. |
| Is another physical engine needed? | No. PostgreSQL plus lexical/vector/relational channels is enough at current scale. | Trace recall/latency/memory usage under real corpus growth and compare the precise bottleneck. | Add an engine only after an existing public SLO cannot be met by the current substrate. |

Two retrieval findings should guide, not dictate, the tests:

- [Practical Code RAG at Scale](https://openreview.net/forum?id=twV78Ytnve)
  finds sparse retrieval strongest and much cheaper for code-to-code retrieval,
  while dense retrieval helps natural-language-to-code at much higher latency;
  simple line chunks match syntax-aware chunks in its setting.
- [Dynamic Context Selection](https://arxiv.org/abs/2512.14313) shows why a
  fixed `k` can introduce distractors and positional bias. It is QA research,
  so MemPhant must reproduce the mechanism on coding tasks before adopting it.

## 9. The next economical experiment

Run one exploratory control-versus-MemPhant pair, then expand only if the
mechanism is visibly alive.

### Task requirements

Choose a genuinely new, complex repository change that:

- is absent from the base commit and has not been solved in prior sessions;
- spans multiple files and at least three of analyze, reproduce, edit, and
  verify;
- combines several relevant historical lessons rather than one generic rule;
- includes one stateful boundary such as migration, restart, concurrency, or
  idempotency;
- requires current official documentation or an internal architecture source;
- has focused checks, adjacent regression coverage, and an independent code
  review; and
- cannot pass by adding one narrow mocked test.

### Frozen arms

- Same fresh base commit, model, reasoning level, task bytes, tools, time,
  permissions, docs, and lifecycle.
- `C0`: memory tools connected to an empty scratch scope.
- `M1`: memory tools connected to the frozen 100-card scope.
- No treatment-only prompt prose. No gold patch, hidden validator, later task
  text, or previous solution transcript in memory.
- The agent may re-query memory as the task evolves; all returned IDs and
  citations are recorded.

### Validation

Use two independent layers:

1. Runnable task-specific and adjacent checks, including real persistence,
   restart, or concurrency behavior where the change requires it.
2. Blind diff review by a capable coding reviewer who inspects requirements,
   architecture, missing cases, and regression risk. A test pass cannot waive a
   critical review finding, and a review impression cannot waive a failed test.

The one pair is exploratory. It opens a six-pair follow-up only if MemPhant:

- returns relevant evidence at two or more useful stages;
- changes at least one material implementation or verification decision for
  the better;
- has no treatment-only critical defect or adjacent regression; and
- keeps added cost and elapsed time reasonable for the task.

If it opens, run six new heterogeneous pairs with at least four treatment wins
and zero losses as the directional gate. A broader claim still requires the
project-cluster design already frozen in
[`2026-08-12-broader-coding-improvement-measurement-design.md`](../superpowers/specs/2026-08-12-broader-coding-improvement-measurement-design.md).

Recommended spend order:

1. `$0` offline card construction, leakage scan, and stage-retrieval replay.
2. At most `$10` for the one live pair.
3. At most `$30` for six more pairs, only if the first pair opens the gate.
4. No broader campaign until the interaction and validators survive review.

## 10. Evidence and privacy contract

Every later claim must keep these boundaries:

- Raw chats, prompts, docs, code patches, commands, and transcripts stay in a
  private checksum-bound location.
- Public artifacts contain anonymous IDs, hashes, counts, aggregate outcomes,
  cost, linkage closure, and scanner results only.
- A terminal task outcome requires external validation; agent self-report is
  never enough.
- Helpful or harmful evidence requires prior recorded exposure.
- Missing task, validator, scope, endpoint receipt, exposure, provider
  settlement, privacy check, restart proof, or spool drain fails closed.
- Negative and null results are preserved. A readiness result is not an
  improvement result; a scoped lesson result is not general agent improvement.
- The current evidence-contract registry and checker remain authoritative for
  any decisional public artifact.

## 11. Non-goals for the first product

- Replacing `rg`, git, language servers, or repository-aware coding tools.
- A universal personal assistant memory claim.
- A graph database, separate vector service, or multi-store federation.
- Automatic ingestion of every transcript or document.
- Automatic promotion based on frequency, similarity, or model confidence.
- A blocking policy/veto engine.
- A dashboard before MCP, files, citations, correction, and forgetting work
  end to end.
- A general coding-agent improvement claim from one task, one lesson, one
  repository, or one model.

## 12. Next-session checklist

1. Read this handoff, the live `STATUS.md`, the
   [capture design](../superpowers/specs/2026-08-09-memphant-capture-design.md),
   and the
   [outcome-coupled flow](../flows/outcome-coupled-evolution.md).
2. Inspect the generated MCP/OpenAPI schemas and the Python SDK before writing
   an adapter. Reuse the seven tools and two outcome endpoints.
3. Freeze one new complex task and its base SHA before building the memory
   corpus or validators.
4. Convert the selected project learnings into 100 cited procedural cards;
   record about ten applicable cards and keep the rest as realistic distractors.
5. Prove no held-out task symbol, file path, test name, literal, patch fragment,
   prompt, or transcript leaks into any card.
6. Run offline stage-aware retrieval and choose the abstaining zero-to-three
   policy without reading live arm outcomes.
7. Preflight the real validators against base-reject and an independently known
   acceptable state. Include adjacent and stateful behavior, not one mocked
   unit test.
8. Run the paired small-model task in independent worktrees with an append-before-call
   spend ledger and no ambiguous retry.
9. Perform blind diff review, reconcile it with runnable evidence, and publish
   a privacy-safe diagnostic whether the treatment wins, loses, or ties.
10. Build capture, promotion, resource lifecycle, or working state only when
    the corresponding gate above opens.

The shortest credible path is not more retrieval infrastructure. It is proving
that a small, well-timed, outcome-backed experience can change a difficult
coding decision without introducing a new defect. Everything else follows from
that result.
