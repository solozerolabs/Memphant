# opencode-config / swarmtools — steal sheet + new-axis analysis

**Date:** 2026-08-08. **Spend:** $0 (source reading only). **Sources:**
`joelhooks/opencode-config` (cloned; `joelhooks/swarmtools` repo is gone/private,
so scoring math read from the published `opencode-swarm-plugin@0.63.2` npm
bundle). **Decisional:** no — this is a research write-up; everything here stays
behind the three gates in [[memphant-adherence-plan-of-record]]
(veto-precision / external-validation / model-replay).

**Standing caveat for everything below:** their system ships **zero evals**. All
thresholds (0.6 inversion, 90-day half-life, ×1.5 proven multiplier) are vibes.
We steal *shapes and formats*, never numbers.

---

## Part 1 — The stealable ideas

Ranked by fit with our adherence lane (adherence, not retrieval — the niche is
what is NOT in the repo).

### S1. Outcome ledger as the deterministic-capture spine (highest fit)

Every task ends with a structured, zero-LLM record:

```
{success, duration_ms, error_count, retry_count,
 planned_files, actual_files, strategy, bead_id}
```

appended to an event-sourced log (append-only `events` table); all "insights"
are SQL views over it (per-strategy success rates, per-file failure history,
top failure patterns). This is exactly our "deterministic capture + async
reflect" spine, and it is a **prerequisite for the model-replay gate** — we
cannot replay-validate candidate rules without an outcome log to replay
against. If we build one thing from this sheet, it's this, because it de-risks
the gate that blocks everything else.

Keying split for us: strategy priors are **per-user** (how this user
decomposes/works), file gotchas are **per-repo**, error-signature patterns are
near-global.

### S2. Scope accuracy — a free adherence metric

`scope_accuracy = |planned_files ∩ actual_files| / |planned_files|`, plus
`time_balance_ratio = max/min subtask duration` and a file-overlap count. Scope
accuracy directly measures "did the agent stay in scope" — a per-task adherence
signal computed from two file lists, no judge, no LLM. Corrections in 32% of
our sessions are rule-violations; this catches the scope-violation subclass
deterministically. Cheapest item on this sheet.

### S3. Anti-pattern inversion → trap-blocks

Per-pattern success/failure counters; at ≥3 observations and ≥60% failure the
pattern is minted as `AVOID: <content>. Failed 4/6 times (67%)` and injected as
a "do not do this" block. Concept fit is exact: a negative rule is
definitionally not in the repo, so grep can't beat us there
([[memphant-grep-beats-us]]), and it concretizes the "always-on trap block"
item in [[memphant-adherence-not-retrieval]].

Their fatal flaw is upstream: patterns are extracted by **12 hardcoded
regexes** over decomposition descriptions, so the system can only ever learn
12 things. The extraction step is where the real work lives (same lesson as
npcpy's extraction-rule prompts). Their inversion math is also statistically
naked — 2 failures out of 3 fires it. Replace the raw ratio with a Wilson lower
bound / Beta posterior; same machinery, defensible trigger.

### S4. Maturity ladder as a context-admission (packing) policy

`candidate → established → proven → deprecated` computed over decayed
helpful/harmful sums, with score multipliers ×0.5 / ×1.0 / ×1.5 / **×0**
(deprecated = hard drop). Read this as a **packing policy**: memory items earn
their prompt slot with evidence, and items with a bad track record are dropped
at admission. Packing is our known bottleneck lever
([[memphant-a1-deep-diagnostic-verdict]], [[memphant-rung7-render-cap-verdict]]),
and our current admit_or_drop gate is content-based, not track-record-based.
Reopening the packing gate requires the quality-delta condition in
[[memphant-packing-gate-verdict]] — but an outcome-linked admission signal is a
genuinely different input than the tie-break quality delta that verdict
contemplated.

### S5. Confidence-scaled decay + validate-to-reset

```
score ×= 0.5^(ageDays / halfLife),  halfLife = 90·(0.5 + confidence)
```

so a confidence-1.0 memory has a 135-day half-life, confidence-0 has 45 days;
an explicit `validate` call resets the clock. One line, composes with our
existing valid-time/generation fields, no schema. Solves stale-preference rot
without deletion (fits evidence-reset-without-machinery-deletion in
[[memphant-product-direction]]). The 90 is a vibe; the *confidence-scaled
shape* and the validate-to-reset affordance are the steal.

### S6. Deterministic triggers instead of semantic recall

Their `error-patterns.md` / `prevention-patterns.md` entries carry a
machine-parseable trigger: `**Error Pattern:** \`Type '.*' is not
assignable\``. Recall fires on **error-text regex match** (and file-gotchas
fire on **file-touch**), not on embedding similarity. This sidesteps the entire
retrieval-quality fight we keep losing to BM25/grep: the trigger is exact, the
payload is the learned fix/prevention. Trigger-condition-as-data is a first-class
field we don't have.

### S7. Git-committed per-repo memory file

`.hive/memories.jsonl` committed to the repo: versioned with the code, shared
through normal git flow, branch/merge semantics for free, **zero server on the
read path**. Strong candidate as the serving format for the Claude Code plugin
shim (spine+shims: the spine stays Postgres; the shim reads a repo-local file).
Also an ops answer: works offline, works in CI, works for teammates who never
installed anything.

### S8. Evidence-annotated injection

Rendered memories carry their track record inline:
`[PROVEN - 78% helpful from 9 observations]`,
`[DEPRECATED - 45% harmful, avoid using]`. The reader model sees the evidence
and can weigh it. Cheap prompt-format idea, testable inside any existing
reader-QA harness as a render variant.

### S9. Human accept/modify/reject as feedback events

`eval_records` carries `human_accepted / human_modified / human_notes` — the
approval lifecycle is a first-class outcome signal (same takeaway we flagged
from npcpy and never built). A human editing the agent's output is the highest
quality "harmful/helpful" label available and costs nothing to record when the
surface already has an approval step.

### S10. Scoped insight queries (steal the scoping, not the injection claim)

Three read APIs at distinct scopes: task-level strategy priors, **file-level
gotchas injected only when a worker is assigned those files**, corpus-level
top-5 failure patterns. Blanket injection measured FLAT on our OctoBench look —
the untested variable is their *scoping* (inject on file-touch, negative-only,
few items). Test under the Phase A live cohort only; do not launch a new paid
injection run for this.

### Explicitly not stealing

- **Hivemind semantic memory** — the where-things-live retrieval lane, measured
  dead three times ([[memphant-grep-beats-us]], [[memphant-xs-crosssession-lane]]).
- **The multi-factor implicit score** (0.4·success + 0.2·duration + 0.2·errors
  + 0.2·retries, thresholds at 5/30 min) — success + error_count carry nearly
  all the signal; wall-clock thresholds are environment vibes. Also has a dead
  zone: a fast, clean *failure* scores neutral, not harmful.
- **Swarm orchestration** itself — orthogonal.
- **Their constants** — all of them.

---

## Part 2 — Is there a new axis?

Framed as: how memory is **received / stored / used** by coding agents, versus
our current substrates (explicit strict-contract client writes → Postgres
units with valid-time/supersession → recall+packing into context, MCP
recall-compat).

### New axis A — outcome-coupled memory lifecycle (the real one)

Today a MemPhant unit's lifecycle is driven by **content-time**: ingestion,
supersession by a newer assertion, valid-time exclusion. In their design the
lifecycle is driven by **measured downstream effect**: every unit accumulates
decayed helpful/harmful evidence from task outcomes, and that evidence — not
content age — decides promotion, decay rate, inversion to a trap-block, or
hard-drop at packing time. Memory units have a *track record*, and the track
record is the lifecycle driver.

This is genuinely new relative to our substrate (we have nothing linking a
unit to the outcomes of tasks it was injected into) and it is the piece that
makes memory *self-correcting*: a wrong preference doesn't wait for a
contradicting assertion to supersede it — it gets harmful-scored out. It is
also exactly the data the model-replay gate needs, so the axis and the gate
share one build (the S1 ledger + a `unit_id ↔ task outcome` join table).

### New axis B — passive cross-agent transcript substrate (CASS)

CASS indexes the session histories of **every coding agent on the machine**
(Claude Code, Cursor, Codex, Gemini, Aider, Cline, Amp, …) and searches them
before solving problems. As *retrieval* this is the dead lane. But as a
**receive channel** it's new: memory acquired with zero client integration —
no bind_context, no capture API, just reading transcript files other tools
already write to disk. For the per-user side of adherence (this user's
corrections, this user's repeated traps, across all their agents) it is the
cheapest possible corpus, and it's the raw material an async-reflect miner
would run over. Caveat: mining ≠ serving; anything mined still enters through
the normal contract.

### New axis C — repo-as-store distribution (half-new)

S7 above, elevated: the git repo itself as a memory *distribution substrate* —
per-repo memory travels with clones, branches, and PRs, and teammates receive
it through `git pull` rather than through a service. Our spine stays Postgres;
this is a serving/sync tier, not a store replacement. New as an integration
surface, not as a storage model.

### Not new (already our axes, reinforced)

- Deterministic capture + async reflect (S1/S9 are implementations of it).
- Trap-blocks / negative rules (S3 concretizes an existing plan item).
- Per-repo runtime profiles (their per-file/per-strategy scoping = our
  profiles, sliced finer).
- Packing as the decisive control point (S4 is a new *signal* into an existing
  axis).

### The one-sentence takeaway

The new thing is **axis A**: close the loop `unit injected → task outcome →
unit evidence → admission/lifecycle decision`. Everything else on this sheet is
either an implementation shape for axes we already hold or a cheap format
trick — and axis A shares its build with the outcome ledger the model-replay
gate already requires, so the marginal cost of the new axis is one join table.

## Suggested order (all gate-respecting, $0)

1. **S1 + S2** — outcome ledger with scope_accuracy, wired into the Phase A
   live cohort capture. Prerequisite for the replay gate; no injection, no
   spend.
2. **A (join table)** — `unit_id ↔ outcome` linkage on the same ledger.
3. **S5 + S6 fields** — decay params + trigger-condition column on units
   (schema only; policies stay off until gated).
4. **S3/S4/S8/S10** — all consumers of the ledger; build only after gate
   evidence exists.
5. **B (CASS-style miner)** — async-reflect input, only once Phase A shows the
   depth signal it rests on.
