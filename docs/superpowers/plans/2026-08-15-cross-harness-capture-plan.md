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

**Phase 2 — demand read (measure, then decide).** From Phase 0/1 telemetry on real Syndai
sessions: how often is a captured memory later recalled AND does it change an outcome
(the coding-lift instrument we already built)? THIS answers the deferred demand question.
If the recall-and-lift rate is meaningful, proceed; if flat, stop — the cheap win is
plumbing, not more pipeline.

**Deferred behind the Phase 2 gate (the devil's cost warning, respected):** the Codex
file-mirror workspace-diff fallback (its hooks can't see `apply_patch`); the opposite-bias
second extractor; the byte-offset resumable tailer (Phase 0 reads the whole transcript at
session end — the tailer is only needed for mid-session/streaming capture); resource/RAG
capture (the one path that genuinely lacks dedup/valid-time — do not ship it broken).

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
