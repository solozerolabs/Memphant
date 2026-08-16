# Cross-harness memory CAPTURE — plan of record (2026-08-15)

Synthesis of an 8-team analysis (codebase, live DB, OSS, web/threat-model, harness
docs, tests, devil's advocate, experimental). This is the write-side counterpart to
the cross-harness INJECTION adapters already shipped (`plugins/`, one shared recall
core). Injection delivers memory; capture is where memory comes from.

## The call (authoritative)

**Build capture — but as a MINIMAL, self-defending instrument, not the full pipeline.**
The devil's advocate is right on one point and wrong on another:

- **Wrong:** that the census NO-GO still kills it. The census stalled because autonomous
  traffic had zero *human* corrections. It does not need them. The agent corrects
  *itself* constantly — self-reverts, red→green tests, later-session contradictions —
  and that is fully observable from git/CI with no human in the loop. The two-mechanism
  design (file-write mirror + session-end summary) is precisely the both-axes source the
  census said was missing. So the historical blocker is dissolved, not stale.

- **Right:** that we are building supply before we have *measured* demand (how often a
  coding task needs non-repo memory). We do not resolve that by arguing — we resolve it
  by capturing. **Capture and the demand measurement are the same instrument:** you
  cannot measure "how often does a captured memory get recalled and change an outcome"
  without first capturing memories. So we build the cheapest capture that produces real,
  trust-laddered memories, and we read its own telemetry to answer the demand question
  the user deferred to "second." If demand is flat, we stop before the expensive tail.

This is not a big bet. Zero schema, mostly reuse, async, and it doubles as the demand
probe. The expensive machinery (four-harness tailer, second LLM extractor, full outcome
plumbing) is explicitly deferred behind a measured gate.

## Open questions, answered

1. **Does the census NO-GO still bite?** No. Weak self-outcome (agent self-revert, test
   transition, later-contradiction, survival) is the correction signal autonomous traffic
   *does* have. "No human corrections" ≠ "no corrections."
2. **Does cross-check actually defeat poisoning?** Not by itself — the devil and the
   threat-model literature agree two low-precision LLM surfaces ANDed don't manufacture
   95% precision, and can co-hallucinate a plausible-false convention (the dangerous
   weak-signal attack; text filters miss it at ~42%). The real defense is the **trust
   ladder + provenance-by-channel**, which the substrate already half-enforces: captured
   agent output lands as `AgentOutput` → forced to inert `candidate` state → invisible to
   high-risk recall until an **independent-family** witness promotes it. Cross-check is
   one witness family, not the whole wall.
3. **Zero-schema or migration?** Zero-schema, confirmed on the live `memphant` schema.
   `memory_unit.state` already has `candidate`/`quarantined`/`validated`; `trust_level`
   is unconstrained free-text (ladder rungs insert today); `reinforcement_count` +
   `confidence` + `last_reinforced_at` drive corroboration; `episode.dedup_key` gives
   idempotent raw capture; `review_event`/`trust_event` are present-but-empty. **No DDL.**
4. **Is the file-write mirror universal?** No. Session-summarize works on all four
   harnesses (each has a Stop/session-end event with transcript access). File-mirror is
   native on Claude Code, opencode, pi — but **not Codex** (its `PreToolUse` fires for
   Bash only; `apply_patch` edits are invisible to hooks). So the mirror is a
   high-precision *augment* where available, not the spine.

## Architecture

**Spine = session-end summarize (universal). Augment = file-write mirror (3/4).** Both
post through the existing `retain`/`remember` verbs — capture writes NOTHING directly;
it inherits every trust/RLS/idempotency/no-resurrection gate for free.

```
session end / file write
   → capture adapter (per-harness, thin; reuses the injection adapter pattern)
      → cheap importance filter (fact-extraction, not "store everything")
      → POST retain/remember  [source_kind = agent/tool/web → AgentOutput floor]
         → lands as state=candidate, trust=captured   (INERT: not served to high-risk recall)
   async reflect job (existing worker):
      → cross-check + weak-outcome witnesses move the item along the ladder
```

**Trust ladder (zero new columns):**
```
[captured] --+witness--> [corroborated] --+independent witness--> [durable]
    |  ^ rescue                |                                      |
    |  |                       | -negative witness                   | -later contradiction
    +--+---------------- [quarantined] --TTL, no rescue--> close (invalidate, not delete)
```
- **captured** = one source, low trust, down-weighted, provisional.
- **corroborated** = one witness (mirror↔summary agreement, OR a positive weak-outcome).
- **durable** = two witnesses from *different families* (source-agreement / weak-outcome /
  survival). The **independence rule** blocks witness-laundering: an agent's file-write
  plus its own summary of that write are the *same* family and count once — a confident-
  but-wrong agent cannot self-promote its own poison.
- **quarantined** = recall-excluded (the teeth), retained for audit, rescuable.

**Anti-poisoning, layered (each maps to a documented intervention point):**
1. Write-time admission: importance/fact filter + composition-risk reject (exists).
2. Provenance by channel: trust tier keyed to source_kind; a fact from a fetched README
   (`web`) can never reach the tier of one confirmed by a passing test. (exists: trust floor)
3. Cross-check (source-grounded): mirror anchors summarizer; agreement must be grounded in
   the observed session, not just two models liking a plausible sentence.
4. Weak self-outcome: retrieved-then-contradicted / self-reverted / test-regressed → cut
   confidence and demote. **Confidence keys off confirmation, never retrieval frequency**
   (defuses the self-reinforcing false-precedent loop — 100% relapse when "fixed" in-chat).
5. Retrieval-time floor: quarantined + below-trust-floor units already dropped from
   high-risk recall (exists).
6. Invalidate, never delete: bitemporal close preserves audit (exists; product direction).

## Latency / performance / cost / UX call

- **Async, off the hot path, always.** Synchronous capture is the documented production
  footgun. Capture never blocks the agent; the user feels zero latency.
- **Cheap by default.** File-mirror is free (no LLM). Session-summarize uses a cheap model
  with a single-pass grounded-consistency gate (~25ms class), not K-sampling. The second
  extractor and heavier checks run only on the ambiguous middle band, gated on a measured
  precision gap — never on every candidate.
- **Best UX = invisible.** The agent learns no new tool, and bad memories are quarantined
  out of sight. Memory only ever *surfaces* (via injection) once corroborated. The best
  capture UX is one the user never notices until a future session is quietly more correct.
- **Mirror vs block (a real fork, decided for UX).** mem0's file-write hook *blocks* the
  MEMORY.md write (exit 2 + a stderr steering message) so its store is the single source
  of truth. We choose the opposite: **allow the write and mirror it** — no denied write,
  no friction, the agent's in-repo note stays useful, and our store captures a copy. The
  only cost is the fact lives in two places; that is acceptable because injection reads
  the store and the file is the agent's own scratch. (mem0's exit-0 `updatedInput` rewrite
  is a third option — allow-but-augment — kept in reserve if we later need to stamp
  metadata in flight.)

## Staged plan (KISS first; each stage gates the next)

**Phase 0 — instrument, CROSS-HARNESS (build now).** One shared capture core
(summarize-last-turn → post `retain`/`remember`) behind FOUR thin per-harness adapters —
the exact shared-core + adapters pattern the injection surface already ships. The
session-summarize spine is universal, so **all four harnesses (Codex, Claude Code,
opencode, pi) capture from Phase 0**, at trust `captured` / state `candidate`. The
file-mirror augment lands on the three where it is native (Claude Code, opencode, pi);
Codex gets summarize-only capture (its hooks can't see `apply_patch`) — a documented
harness limitation, not a scope cut. The KISS cross-check: `mirror ∧ summary agree →
corroborated`; `one source → captured`; `cross-source subject collision → quarantined`;
recall filter excludes quarantined. Zero new columns, one reflect-job CTE, one recall
WHERE. Emit capture telemetry (what was captured, from which source/harness,
promoted/quarantined, later recalled?).

**Phase 1 — weak self-outcome (build now, pure git/CI, no LLM).** Self-revert + test-
transition detector at session end writes a `review_event`; the reflect job uses it as the
promotion/demotion witness. This is the highest-ROI anti-poisoning signal and it's ~free.

**Phase 2 — demand read (census → observe → single-component pilot).** NOT a binary
full-tail GO: the instrument observes the value of memory we *already* capture, while the tail
changes capture coverage/quality — a different quantity. So Phase 2 is a feasibility census (is
this even measurable at Syndai's independent-subject scale?), an observational read (estimate
recall rate × lift × outcome value), then an isolated causal pilot of the *single cheapest tail
component*, which is the only stage that can license "build this one component." Each remaining
component earns its own pilot only if the first pays. **Executable design: see "Phase 2 —
demand read: executable design" at the end of this doc.**

**The tail = the pilot candidates, one at a time (the devil's cost warning, respected).**
These are no longer "deferred behind one gate" — each is a *single-component pilot candidate*
Phase 2 chooses among (Stage 1 fingers whichever most limits recall): the Codex file-mirror
workspace-diff fallback (its hooks can't see `apply_patch`); the opposite-bias second extractor;
the byte-offset resumable tailer (Phase 0 reads the whole transcript at session end — the tailer
is only needed for mid-session/streaming capture); resource/RAG capture (the one path that
genuinely lacks dedup/valid-time — do not ship it broken). Only the one that wins its pilot gets
built; the rest wait for their own.

## What we explicitly do NOT build

- No new schema/migration. No new service or MCP verb (ride `retain`/`remember`/`reflect`).
- No blocking/denying agent writes (mirror is observe-and-copy).
- No hard deletes (invalidate + bitemporal close).
- No "store everything" — the importance/fact filter is mandatory; repo-recoverable facts
  are deliberately not captured (grep wins there).
- No second LLM extractor until a measured provisional-lane precision gap justifies it.

## Reference implementations (licensed, copy with attribution)

- **cognee** (Apache-2.0) — `tasks/memify/apply_feedback_weights.py` + `api/v1/improve/improve.py`:
  the working prior art for the outcome→provenance→weight cross-check. Record
  `used_memory_ids` per served answer; on an outcome, EMA-update a `feedback_weight ∈ [0,1]`
  on exactly those memories (`w = w + α(rating−w)`, α=0.1), idempotent via a
  `weights_applied` flag; gate recall on the weight. This is the concrete shape for Phase 1
  (weak self-outcome → down-weight the memory that produced a reverted change). Direct SQL fit.
- **memsearch** (MIT) — Stop-hook last-turn summarizer (`plugins/claude-code/hooks/`,
  `prompts/summarize.txt`) and the content-addressed upsert PK (`sha256(source+span+content+model)`,
  `ON CONFLICT`). Copy the summarizer prompt (external-observer, 2–10 bullets, "do not
  answer") and the deterministic dedup PK.
- **mem0** (Apache-2.0) — the two Claude Code hook contracts (exit-2-stderr redirect,
  exit-0-`updatedInput` rewrite) and the capture-exclusion filters (below).
- **graphiti** (Apache-2.0) — soft-expire on contradiction (`invalid_at`/`expired_at`, never
  DELETE); reuse our existing valid-time for this.
- **supermemory** (MIT) — inferred-until-confirmed down-weighting (auto-captured ranks below
  stated facts until confirmed).

## Capture filters — what we deliberately do NOT store (from mem0/memsearch)

Mandatory write-time exclusions (cheap, pre-store, no LLM): **secrets** (regex-redact
`ghp_`/`xox[baprs]-`/`sk-…` etc. before store), **echoes** (assistant restating the user's
own words — "no echo extraction"), **phatic/filler** ("Sure!", greetings, ack), **tool
noise** (tool-call/tool-result/thinking blocks stripped from the summarizer input),
**trivial turns** (length gate: skip `<N` lines), **subagent sessions** (skip when an
`agent_id` is present), and **repo-recoverable facts** (grep's turf — our differentiated
exclusion; capture only what is NOT in the repo).

## Test plan (non-vacuity mandatory)

New `crates/memphant-core/tests/capture_crosscheck.rs` + a poisoning lane in
`examples/evals/security-smoke.yaml` with a `no-crosscheck` control arm (modeled on
rung6's `no-edges` control, so the eval runner refuses promotion if the control is
missing). Every test pairs a positive assertion with a removal-perturbation:
- poison flagged by one source → quarantined + recall-excluded; **control: disable
  cross-check → poison survives** (the load-bearing non-vacuity test).
- corroborated good memory kept (false-positive guard); single-source high-trust user
  memory kept; control: flip trust/diverge bodies → now dropped.
- capture respects: trust floor/clamp, RLS/subject isolation, idempotency (auto-capture is
  the highest-volume replay surface), **no-resurrection** (a file still containing a
  forgotten fact must not resurrect it on next sync — the sharpest mirror false-positive),
  preference/source-kind gate, `file_sync` fail-closed.
- Run against BOTH InMemory and Postgres stores (store-divergence rule): capture's
  dedup/promotion reads via the write seam (`fetch_scope_open_units`), never the bounded
  recall pool.

## Phase 2 — demand read: executable design

**What the decision actually is (reframed — the estimand correction).** The naive framing
"measure whether captured memory pays off, then GO/STOP the whole tail" is the *wrong estimand*.
The instrument can only observe the value of memory *we already capture*; the deferred tail
(Codex diff-fallback, second extractor, resumable tailer, RAG capture) changes capture *coverage
and quality* — a different quantity. High observed lift could mean the cheap path already grabs
everything valuable (tail not worth building); low recall could mean the missing tail *is* the
bottleneck (tail exactly worth building). So a binary full-tail GO is unsupported. **The most a
demand read can license is the single cheapest tail component, run as a marginal-value pilot.**
Phase 2 is therefore three stages: a **feasibility census** (is this even measurable at Syndai's
scale?), an **observational read** (estimate the multiplicands), and a **single-component pilot**
(the only thing that can say "build this one component").

```
Stage 0  FEASIBILITY CENSUS ($0)           can we even measure it here?
  independent-subject count · ICC · max attainable power
  + stand up exposure-receipt + shadow-retrieval eligibility
        │  underpowered / not observable → STOP (revisit at more traffic; do not spend)
        ▼
Stage 1  OBSERVATIONAL READ ($0)           estimate the pieces of tail value
  cross-session recall-of-captured rate · causal-lift-per-eligible-recall (obs.)
  · weak-outcome density · which missing tail-component most limits recall
        │  hash the artifact = PREREG #1 (frozen before any Stage-1 look)
        ▼
Stage 2  SINGLE-COMPONENT PILOT (causal, FRESH traffic)   the only GO-capable stage
  build the cheapest tail component Stage 1 fingered · isolated per-subject holdout
  · terminal fully-observed outcome · PREREG #2 (sized from Stage 1, new non-overlapping traffic)
        │
        ▼  GO(component) iff  incremental_eligible_recalls × lift_per_recall × outcome_value
                              − component_cost  clears materiality δ, CI-backed.
           Never a binary full-tail GO. Each component earns its own pilot.
```

### Reuse — zero new plumbing except one exposure receipt

Everything the read needs already exists, with **one** genuinely new instrument:
- `memphant.retrieval_trace` (served unit ids in `candidates`/`citations`) = the recall
  *candidate* signal — necessary but **not sufficient** (a candidate is not proof the unit was
  rendered, fit the context budget, and was seen before the action). **New (small): an
  exposure receipt** logged by the injection hook — eligible ids, actually-rendered ids, token
  position, arm, and immutable session+task ids — so treatment is observed, not assumed.
- `payload.capture` provenance = the *captured*-vs-seeded discriminator.
- `memphant.review_event` weak self-outcome = the outcome signal; `memphant.mutation_ledger`
  = write-back.
- `scripts/mcp_usage_report.sql` = the read to extend (`demand_funnel.sql` shares its
  subject/since params and served-unit CTE, does not re-derive them). Injection hooks +
  `dogfood_bootstrap.sh` = the live wiring already merged.

New artifacts, total: the exposure receipt (hook-side), `scripts/demand_funnel.sql`, **two**
preregistrations (Stage 1 pre-look; Stage 2 sized from Stage 1's hashed artifact, on fresh
traffic — one prereg cannot both precede Stage 1 and freeze Stage-2 params from Stage-1 data),
and a `sha256(subject_id) mod 2` arm split. No schema, no service verb, no labeling.

### Stage 0 — feasibility census (is it measurable here at all? $0, do FIRST)

The subject census is load-bearing: effective N is *independent subjects*, not sessions —
many sessions from a few subjects add almost no power under within-subject correlation.
Before any measurement, compute from existing traffic:
- **independent eligible subjects** (distinct coding `data_subject_id`s with real coding
  sessions), **task-episode count**, **weak-outcome density** (terminal outcomes per 100
  sessions), and an **ICC** estimate → feed `instrument_power.py` for **maximum attainable
  power**. If the ceiling can't detect a material effect, **STOP** — the honest result is "not
  measurable at this scale," not a fabricated GO. Revisit when traffic grows.
- Stand up the **exposure receipt** and **shadow-retrieval eligibility** (run identical
  retrieval in both arms, record which episodes are eligible) so Stages 1–2 observe treatment
  correctly and define eligibility identically across arms (prevents the treatment-affected-
  denominator trap: injection changes downstream code/captures/retrieval, so eligibility must
  be fixed by shadow retrieval *before* suppression, analyzed ITT over eligible episodes).

### Stage 1 — observational read ($0; estimates the tail-value multiplicands)

Over existing tables, attribution **cross-session** (a later session serving an earlier
session's captured unit; the causal claim is Stage 2's, never the funnel's):
1. **captured** → **promoted** (`corroborated`/`durable`) → **recalled-captured** (a promoted
   unit *delivered* — per the exposure receipt, not merely a candidate — in a later session's
   recall for the same subject) → **outcome-coupled** (followed, forward in time, by a
   *terminal fully-observed* weak-positive: change produced, prescribed validation attempted,
   result known, no censoring — not merely "no revert seen", which encodes absence of
   observation).
2. Estimate the two multiplicands the pilot needs: **recall-of-captured rate** and an
   observational **lift-per-eligible-recall**. And fingerprint **which missing tail component
   most limits recall** (coverage gap on Codex apply_patch → diff-fallback; precision on the
   ambiguous band → second extractor; mid-session latency → tailer) so Stage 2 pilots the
   cheapest high-leverage one.

**Controls (non-vacuity, perturbation-based):**
- *Capture-OFF subject* must read `recalled-captured = 0` (else the provenance join leaks).
- *Within-stratum shuffle:* re-pair recalls with outcomes **within subject × task × time
  block** (a global shuffle destroys structure and proves little); coupling must collapse to
  chance. Validates the join, not yet the causal construct.

Freeze Stage 1's output as a **hashed artifact = preregistration #1**, committed before the
first look (the `a2fad02f`/`601fdb5c` discipline).

### Stage 2 — single-component pilot (the only GO-capable stage; causal, fresh traffic)

Build the one cheapest tail component Stage 1 fingered, then measure *its marginal value* on
**new, non-overlapping** traffic (preregistration #2, sized from Stage 1).
- **Isolate capture, not injection-at-large:** contrast *capture-sourced injection on* vs
  *suppressed*, seeded memory served identically in both arms, so the delta is attributable to
  captured units — and specifically to the units the new component adds.
- **Randomize per subject** (`sha256(subject_id) mod 2`, a real hash so time-ordered ids can't
  correlate the arm with batching) — the shared per-subject store means a session-level split
  lets a holdout's captures leak into a later treatment session. Report arm balance across
  strata. (Per-subject costs N; that is exactly why Stage 0's subject census gates this.)
- **Primary = capture-attributable outcome-coupling delta** on a **terminal fully-observed
  outcome** (no proxy switching — the repeated-question fallback may seed *power* but cannot
  authorize GO, as it changes the scientific question).
- **GO(component) iff** `incremental_eligible_recalls × lift_per_recall × outcome_value −
  component_cost` clears a preregistered **materiality δ** (the value that makes *that
  component* worth its build+run cost), CI-backed with a sequential-look α guard (the
  usage-cap-interrupted Horizon run is the cautionary case). Never a binary full-tail GO —
  each remaining component earns its own pilot only if the first pays.

### Cost, calendar, invalidators

Marginal spend ≈ **$0** through Stage 1 (observational + hooks + weak-outcome pure git/CI; the
one new cost is the small exposure receipt). Stage 2 spends only the build of *one* cheap
component. Real cost is **calendar** to accumulate independent-subject N — which is precisely
why Stage 0 computes the power ceiling before anything. Invalidators, all in our ledger of scars:
- *Too few independent subjects* → Stage 0 STOP (don't fake power with session count).
- *Saturation regime:* Syndai tasks rarely need non-repo knowledge → flat for a true reason
  (reconciles the OctoBench null) → legitimate STOP.
- *Store-divergence / stale-binary:* run against the served path (superuser bypasses RLS);
  rebuild `memphant-mcp` after any merge or it returns the pre-identity-free `missing field
  subject_id`.

### Open questions — resolved (authoritative calls)

| Question | Call |
| --- | --- |
| Decision shape | **Not binary full-tail GO.** Census → observational → single cheapest-component pilot; each component earns its own pilot. |
| Feasibility precondition | **Independent-subject census + ICC + max attainable power FIRST.** Underpowered → STOP, don't fake it with session count. |
| Is a recall "delivered"? | **Exposure receipt** (rendered ids + token position + arm), not a bare `candidates` membership. |
| Eligibility across arms | **Shadow retrieval before suppression**, identical both arms; ITT over eligible episodes (kills the treatment-affected denominator). |
| What the causal arm contrasts | Capture-sourced injection **on vs suppressed**, seeded held constant. |
| Randomization unit | **Subject** (`sha256(subject_id) mod 2`); session-level only for the coarse injection-in-general secondary. |
| Attribution window | **Cross-session**; causal claim carried by the Stage 2 holdout only. |
| Primary outcome | **Terminal fully-observed** weak-positive (validation attempted + result known, no censoring). No proxy switching for GO. |
| Preregistration | **Two:** #1 pre-Stage-1 (hashed artifact); #2 sizes Stage 2 from #1, on fresh non-overlapping traffic. |
| Shuffle control | **Within subject × task × time stratum**, not global. |
| GO threshold | Component's `incr_recalls × lift × value − cost` clears a preregistered **materiality δ**, not p<0.05 vs 0. |

### What Phase 2 deliberately does NOT do

No new schema, no service verb, no manual labeling, no synthetic tasks, no proxy-switching to
authorize GO, and **no binary full-tail GO** — the estimand can't support it. Stage 0 can STOP
on infeasibility; Stage 1 estimates but never concludes; only an isolated single-component
Stage 2 pilot on fresh traffic can say "build this one component."

## Phase 3 — Memory substrate: types, stores, consolidation (8-team synthesis, 2026-08-15)

Prompted by the first end-to-end cross-repo battery. Eight teams (codebase, live DB, tests,
Context7 docs, 2026 research, OSS code audit, experimental design, devil's advocate) answered:
*why does capture store one type via one channel, is the substrate itself limited, what else is
broken, and what should the long-term default be?* Everything below is evidence-backed
(file:line / SQL / URL in the team reports); this section is the authoritative distillation.

### The diagnosis (authoritative)

**The substrate is rich; the live path is thin.** The question was framed as "only 1 memory type,
only 1 storage mode" — the schema disagrees: `MemoryKind` has 6 kinds (Episodic, Semantic,
Procedural, Belief, Resource, Preference), `MemoryEdgeKind` has 6, units are bitemporal, every unit
gets a pgvector embedding + BM25 tsv, and FSRS columns exist. What is limited is **routing and
wiring**, not schema:

| Layer | Exists? | Wired on the live capture→inject path? | Evidence |
| --- | --- | --- | --- |
| Kinds | 6 | capture mints **Belief only** — by design ("single provisional claim", service.rs:7805-7813); Procedural has no live mint site outside explicit `remember` (0 voluntary calls in dogfood); Semantic effectively dead (fact extraction default OFF); Preference never minted by agents | codebase §1 |
| Channels | 2 (summary, mirror) | Codex = **summary only** (`apply_patch` invisible to hooks) → SourceAgreement witness impossible there | codebase §1, plan §Arch |
| Vector | written for every unit | **disabled on the served coding lane** — 3 embeddings written, never read; recall = FTS + recency + `ilike(fact_key)` | DB §4 |
| Graph (`memory_edge`) | 6 kinds, read at recall | only when `edge_expansion_enabled` (**default false**); capture writes 0 edges; Belief arm has **no supersession** (`supersedes_own_kind → None`) | DB §4, codebase §1 |
| Consolidation | columns exist | **`reinforcement_count/stability_days/last_reinforced_at/difficulty/freshness_due_at` have NO UPDATE path anywhere** — write-once dead weight; `AdmissionAction::Merge` is a **no-op**; body-merge needs byte-exact equality; same-subject recapture = 24h idempotency replay, then `compact_valid_excl` **error** | DB §3/§5, codebase §3 |
| Trust ladder | full state machine | **bypassed**: retain never clamps to `source_kind`, dogfood/battery actors are `system`/`trusted_system` → captures born `active/trusted_system`, `Promote` (needs Candidate) moot | DB §6, codebase §4b, devil §4 |
| Episodic | kind + table | compiler mints episodes **1:1**, no rollup/aggregation stage; captures deliberately skip the Episodic unit copy | codebase §1 |
| Dead tables | 4 | `belief_observation, trust_event, blob_ledger, event_outbox` — zero references in any crate | DB §2 |

So capture = one channel + one kind + one card, on a lattice whose consolidation, vector, graph and
trust layers are all present and all dormant. Belief-only was **faithful design intent** (KISS
demand probe); everything else in the table is accident or never-finished.

### New defects (beyond the four fixed today: include_beliefs, md-bullet false-drop, observed_at="", compact=None)

| # | Sev | Defect | Where | Minimal fix |
| --- | --- | --- | --- | --- |
| P0-A | **CRITICAL** | **Design deadlock.** Enforcing the intended trust floor (AgentOutput→Candidate) makes captures **unrecallable** (`recallable()` needs Active/Validated) → never reviewed → no WeakOutcome; SourceAgreement impossible on Codex; the plan's **"survival" witness is not implemented**. Captured memory on Codex would be permanently inert; today it is served *only because* the floor is broken. | lib.rs:10841-10844, 12641-12648, 12882+; plan:80 | Serve `Candidate` captured units on the coding lane **labelled unconfirmed** + implement the survival witness (served → task ended without revert/contradiction ⇒ WeakOutcome promote). |
| P0-B | HIGH | Idempotency key = `capture:{source}:{subject}` → any stable subject with a different body ⇒ ledger hash mismatch ⇒ **409 silently swallowed** (capture lost). Fires on *every* capture once the subject key is stabilised. | memphant_capture.py:444; store.rs:1452 | Key on `sha256(body)`; subject is identity, not idempotency. |
| P0-C | HIGH | Capture body cap 8192 chars vs MCP recall budget **512 tokens** (`max(words, len/3)`) ⇒ any summary >~1500 chars is a silent Budget drop, un-injectable; capture skips the ceiling check `remember` enforces. | memphant_capture.py:59; mcp/lib.rs:223,632; lib.rs:9695-9718 | Enforce `MCP_COMPACT_TOKEN_CEILING` in `capture_episode_candidate` (and cap summarizer ~1200 chars). |
| P1-A | HIGH | Trust floor unenforced (retain uses `assigned_trust` only, never clamps by `source_kind`); battery key `--max-trust trusted_system`. Anti-poisoning is off in the only e2e harness. | service.rs:3872-3990; run_battery.sh:60,77 | Clamp `assigned_trust ≤ actor_kind_trust(source_kind)` in retain; battery key `AgentOutput`. |
| P1-B | HIGH | Fragmentation: subject key = first 8 tokens of the summarizer's first line (wording-dependent) — the body-derived identity the code comment says it must not be. Result: 3 units for one contract, `reinforcement_count=0`, 0 edges, and the 1-card lane serves an arbitrary fragment. | memphant_capture.py:287-296; service.rs:7838 | Deterministic stable key (below) + reinforce-on-same-source-repeat + supersede-edge. |
| P1-C | MED | `limit: Some(1)` on the coding lane; `dropped: output_limit` on every trace. Every peer defaults 5–10 budgeted. | mcp/lib.rs:631 | `limit: Some(3)` under the same 512 budget (max 5). |
| P1-D | MED | Belief→Semantic promotion unreachable for captures (`is_independent_source` needs differing `source_kind`; all captures are `agent`). | lib.rs:13664-13675 | Independence = differing **channel** (`payload.capture.source`) or outcome witness, not `source_kind`. |
| P2 | LOW | `confidence: Some(1.0)` on summarizer output (contradicts "confidence keys off confirmation"); compact `verification` empty; fact_key stored token-normalised so `ilike` family only matches normalised queries. | service.rs:7845, 7827 | 0.5; leave verification empty (captures have none); note. |

Devil's-advocate finding adopted verbatim: **the file arm is the real control**, not bare — memphant
must beat a `MEMORY.md` written by the same summarizer, or Postgres is governance infrastructure for
an unproven need.

### What the evidence says (three independent lenses agree)

- **Value axis = efficiency on complex tasks, not correctness.** Sandelin's 2026 controlled coding-
  memory bench: quality identical across {none, memory, curated file}; memory saved **22–32 % cost /
  28–40 % turns on complex tasks, 0 on simple** — an exact replication of our 6/6-correct + x1/x3
  overhead. SWE-ContextBench: *free* agent-chosen reuse **net negative** (+24 % cost, no gain); only
  oracle selection helps ⇒ **precision over recall**. Cross-domain-transfer + ReasoningBank:
  **abstracted insights help; raw low-level traces cause negative transfer** ⇒ episodic-as-raw-card is
  measured-harmful.
- **Consolidation is moving to ADD-only + supersede-by-time + links.** mem0 v3 (63k★) rolled back
  LLM UPDATE/DELETE to additive + md5 dedup + `linked_memory_ids`; graphiti never deletes (`invalid_at`
  by valid-time order); MemOS archives losers with `MERGED_TO`; langmem `enable_deletes=False`.
  **Stable identity keys are rule-based** (cognee `uuid5(normalised name)`, graphiti normalise→MinHash),
  similarity thresholds (0.6/0.8/0.85) are only a *candidate gate* before a judge. This is exactly
  MemPhant's bitemporal row + unused edge table.
- **Coding-native memory converged on flat-file pinned tier + searched tail.** Claude Code
  (CLAUDE.md/MEMORY.md), Letta Code (MemFS in git, `dream` reflection, `reason` = commit message),
  OpenHands MEMORY.md, LongMemEval-V2 (agent-over-files 72.5 % > RAG store 48.5 %). Taxonomy that
  survives: prefs/conventions/**gotchas**/tooling commands/architecture — not transcripts. Injection
  clusters at **5–10 items** budgeted with recency decay + MMR.
- **Poison is real and persistent** (HEARTBEAT: routine saves persist pollution up to 91 %; MINJA >95 %)
  ⇒ keep the ladder, provenance caps, quarantine; never inject-by-default without a label.
- **No neutral evidence that graph beats vector for coding memory**; modality rankings reverse across
  model families (MemDelta); embedding choice > architecture. Graph pays only for genuinely temporal /
  conflicting facts — MemPhant's trust-ladder niche.

### The call (authoritative) — long-term default balancing latency, performance, cost, UX

1. **Types: three, chosen deterministically by CHANNEL — no LLM classifier.** A classifier is a second
   hallucination surface (haiku already fabricated conventions) and every OSS system that types by
   LLM is moving away from it.
   - `belief` ← session-summary channel (external contracts, env/system facts, cross-repo). LLM stays
     here only (one flash-lite call/session).
   - `procedural` ← **deterministic** triggers: (a) error→fix pairs (tool `exit≠0`/traceback followed
     by the edit/command that made it pass, zero LLM); (b) the same normalised command sequence in
     ≥3 sessions ⇒ *one* LLM call writes the skill text (skill crystallisation, Voyager/SkillOps
     pattern); (c) file-mirror of workflow notes where hooks allow. Served Active on the coding lane
     (already true), labelled.
   - `preference` ← explicit user `remember`/correction only (unchanged).
   - **Episodic = provenance rows, never units or cards.** Keep the raw `episode` (with `capture://`
     source_ref) as the witness/lineage a belief points to; do not mint Episodic units or inject them
     (raw traces are measured-harmful).
   - `resource` (cached Context7/web slices keyed `(library, version, query)`) is **gated** to Stage D:
     real value hypothesis (user's), but no repeat-rate number yet.
2. **Stores: Postgres stays source of truth; add a flat-file projection as the primary delivery.**
   Render *durable-rung* units (and the pinned tier: top prefs + gotchas ≤ 2 k chars) to
   `.memphant/MEMORY.md` per repo + `~/.memphant/MEMORY.md` cross-repo, from a reflect-job renderer;
   the agent greps it for free (zero hot-path latency, transparent/editable UX, works on hookless
   harnesses, converts "grep beats memory" from competitor to channel, sidesteps the 1-card drop).
   MCP `recall` stays for cross-machine/tenant with `limit 3` (max 5) under 512 tokens. **Vector:**
   enable on the coding lane (embeddings already paid for). **Graph:** populate `supersedes`/
   `same_subject` edges at capture consolidation (cheap, `insert_edge` exists); keep
   `edge_expansion` off until a temporal-conflict use case is measured. **FSRS:** wire the UPDATE
   path (reinforce/decay) — the columns exist for this. **Delete** the 4 dead tables (pre-prod).
3. **Consolidation: two-stage, rule first, LLM only in the ambiguous band; ADD-only, never delete.**
   Stage 1 (deterministic, $0): `subject = repo_slug + normalised topic key`; same source + same key
   ⇒ **reinforce** (`reinforcement_count++`, `last_reinforced_at`, body refresh as a new row +
   `supersedes` edge to the old — no overwrite, no delete); idempotency = `sha256(body)`. Stage 2
   (gated): cosine ≥ 0.8 top-5 as a *candidate gate* → LLM DUPLICATE/UPDATE/NEW judge (MemOS/crewAI
   pattern) — only after Stage 1 shows fragmentation still matters. Different-channel same-key stays
   the SourceAgreement witness (unchanged, per the coexistence tests).
4. **Trust: enforce the floor AND make the ladder serviceable (resolves P0-A).** retain clamps to
   `source_kind`; captured units land `Candidate`; the coding lane **serves Candidate captured units
   with a visible `[unconfirmed]` label** (precision-gated as today — abstain when unsure); a
   **survival witness** promotes: served → task ended green with no revert/contradiction ⇒ WeakOutcome
   ⇒ Validated. Independence for promotion = differing *channel* or an outcome witness. Confidence
   prior 0.5. Battery/dogfood keys clamped to `AgentOutput`.
5. **Injection UX:** one advisory block, ≤ 3–5 items, ≤ 512 tokens, ranked by trust rung ×
   reinforcement × recency (14 d half-life), each item labelled confirmed/unconfirmed, valid-time
   shown when superseded; log which items were *used* (cognee `used_element_ids`) to feed the ladder.
   Plus the always-on pinned tier via the file projection. Precision over recall: fewer, surer cards.
6. **Measure with the right control before expanding further.** The decisive tranche (~$250):
   arms {bare, memphant-3card, MEMORY.md-projection} × tasks {cross-repo `xrepo` (exists), env
   gotcha `tzgotcha` (exists)}, **n = 15 paired same-task** (paired SD ≈ 60 k ⇒ detects ≈ 15 %),
   memory pre-seeded from a prior seeding session (variance control), arm order randomised, cold
   homes, tasks authored blind to seeds; endpoints preregistered: Δtotal_tokens/Δtool_calls
   conditioned on judge-correct (primary), correctness, poison-follow rate; Wilcoxon signed-rank;
   **go bar = ≥ 15 % median token reduction AND memphant ≥ file arm.** Second tranche (~$150) adds
   error→fix + skill arms only on signal.

Latency/perf/cost summary: capture is async and mostly deterministic (LLM = one flash-lite call per
session for beliefs + one per skill crystallisation ≈ fractions of a cent); hot-path latency 0 via
the file projection, ~30–50 ms via MCP; storage cost unchanged (Postgres + one embedding/unit);
UX = the agent greps a file it already trusts, sees confirmed vs unconfirmed, and the user can read/
edit/delete memory as text while Postgres keeps trust, provenance, tenancy and time.

### Open questions — answered

| Question | Answer |
| --- | --- |
| Why one type / one channel today? | Design intent (KISS demand probe: "capture = single provisional claim", summary spine + mirror augment). Belief-only + first-line key + 1 card were the accidents inside that intent; the dormant substrate (no reinforcement UPDATE, vector off on the lane, no belief supersession, unimplemented survival witness) is unfinished wiring, not decisions. |
| Is it storing things only one way? | Yes on Codex (summary only); mirror elsewhere. Fix = deterministic channels (error→fix, repeated-command, fetch) + the file projection, not more LLM channels. |
| Should capture classify type? | Yes — **by channel, deterministically**. Never by an LLM classifier. |
| Flat files vs DB vs vectors vs graph — which? | **All, in roles:** Postgres = truth (trust/provenance/time/tenancy); flat file = delivery + pinned tier; vector+BM25 = the searched tail (enable vector on the lane); graph = supersession/lineage edges only, no traversal until a temporal-conflict case is measured. Not a competition. |
| Test all combinations? | No — the field's evidence already ranks them; test the two live questions (N-card vs 1-card; file vs MCP) at n = 15 first, then expand only on signal. |
| Are there more bugs? | Yes — P0-A/B/C + P1-A..D above; the reinforcement columns and `Merge` are dead code; four dead tables. |
| Best UX? | Memory the agent already trusts (a greppable file), small labelled advisory cards, never silent, always editable; measured on cost/turns, not claimed on correctness. |

### Staged plan (each stage gates the next; $0 until Stage C)

- **Stage A — make the loop honest ($0, tests first).** P0-B idempotency→body hash; P0-C ceiling in
  `capture_episode_candidate`; P1-A trust clamp + battery key AgentOutput; P0-A Candidate served
  labelled + survival witness; stable subject key + reinforce-on-repeat + `supersedes` edge (real
  `Merge`); vector on the coding lane; `limit 3`; confidence 0.5. Tests (tests team, ranked): MCP
  `recall()` hit on a captured belief; core coding-lane recall with `include_beliefs` perturbation +
  `compact.is_some()`; bulleted-prose vs bulleted-code `is_repo_recoverable` pair; poster payload
  shape + `observed_at=""` rejected server-side; PG clone via `with_scratch_db`; same-source
  recapture ⇒ 1 unit `reinforcement_count=2` (scoped so the coexistence tests still hold).
- **Stage B — channels + projection ($0).** Deterministic error→fix procedural capture; repeated-
  command skill trigger; `MEMORY.md` projection renderer (durable rung + pinned tier); `[unconfirmed]`
  labelling in the advisory block; delete the 4 dead tables.
- **Stage C — the decisive experiment (~$250, gated on Sid).** As specified in call #6.
- **Stage D — only on signal.** LLM dedup judge for the ambiguous band; `resource` fetch cache;
  `edge_expansion`; Semantic promotion of captured beliefs.

### What Phase 3 deliberately does NOT do

No LLM type classifier; no Episodic units or episodic cards; no graph traversal at recall; no
LLM UPDATE/DELETE of memories; no new harness adapters before Stage C reports; no correctness claims.
