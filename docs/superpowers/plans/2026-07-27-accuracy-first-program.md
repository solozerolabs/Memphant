# Accuracy-First Program — small decisive tests before big runs

Date: 2026-07-27
Status: APPROVED FOR EXECUTION (tri-lens final review passed; does not touch STATUS.md checkboxes)
Method: 7-lens research fan-out (library docs, 2026 papers/blogs, OSS repos, codebase,
tests/datasets, experimental design, platform surfaces) + user-learning investigation
(Tacitry, power-user corpus census, 2026 user-voice research) + adversarial
verification + tri-lens final review (July-2026 currency, goal alignment, cohesion).
Every load-bearing claim was verified against a repo file or is cited to one.

## Goal

Measured accuracy wins for the users we actually have — coding agents / the CaaS
system, evolving into agentic chat over codebases, tasks, and docs — proven on
neutral instruments, with light-before-heavy spend and preregistered kill gates.
SOTA claims are downstream evidence, never the objective.

Priorities: accuracy > cost > perf/latency — with latency governed as a
preregistered hard ceiling, not an optimand. The served recall path runs p50 ~34ms
against 200/500ms SLO bars, and this repo already retired its largest QA lever (the
13s cross-encoder) for breaking a latency bar. Every default flip in this program
re-runs the $0 SLO harness and records the reader-token delta beside its accuracy
endpoint. Budget is not the binding constraint (owner-confirmed); question exposure,
golden-bank quality, and evidence credibility are — so the free gates and kill gates
stand at any budget.

Model representativeness is a standing rule: any run whose verdict can flip a
production default uses a reader that represents deployed traffic —
`claude-opus-4-8`, the Claude Code executor's workhorse default — either as the
primary reader (Phase 3) or as the mandatory robustness arm (Phase 2). Eval-only
lattices are legitimate for screening and for reusing frozen evidence, never as the
sole basis for a promotion.

## Research sufficiency

**No further paper/blog research is needed to execute.** A fresh June–July 2026
sweep confirmed: the LongMemEval-V2 leaderboard is still empty; no coding-domain
correction-retention benchmark exists (MemSyco-Bench arXiv:2607.01071, BenchPreS
arXiv:2603.16557, and PERMA arXiv:2603.23231 are all chat-domain); the Always-On
Agents survey (arXiv:2606.30306) names our exact gap — coding agents that "repeat
known mistakes" — and offers no instrument. The first-mover slot is open and the
field is circling it: this argues for pace, not more reading. External inputs that
shaped the design: cross-domain memory transfer to coding is weak (+3.7% avg,
negative transfer from low-level traces — arXiv:2604.14004), and no memory-
condensation strategy improved coding-agent quality on DiscoveryBench
(arXiv:2605.18854) — so each lane must earn its own defaults.

**Auto-research is rejected for these runs.** Best measured LLM ablation-planning
accuracy is ~45% (AblationBench v3); at our scale (a handful of paired runs,
one-shot sealed sets) the scarce resources are exposure and credibility, not design
bandwidth. We keep the automation that pays: agent-adjudicated mining with human
spot-checks, deterministic $0 replays, frozen same-lattice judging, and analysis
code committed before unblinding.

## Evidence corrections this plan rests on (verified)

1. **The pack_render_cap "abstention regression" was not an abstention regression.**
   The 3/4→1/4 flip on the n=12 screen is a retrieval-only proxy: `score_question`
   in `crates/memphant-eval/src/bench_lme.rs` (~line 611) scores an `_abs` case
   correct when `abstained || first_answer_rank.is_none()` — no model ever
   abstained. Cap-1200 packs 7–9 items instead of 4–5, so the near-miss trap
   sessions surface at rank 1–2 and the proxy flips. But the LME gold answers for
   those sentinels *require the trap content in context* ("You mentioned your cat
   Luna but not your hamster") — a reader needs the trap to abstain correctly. The
   gate punishes admitting the evidence the answer needs, and with 2 discordant
   pairs (McNemar exact p=0.5) it had zero power. Artifacts:
   `docs/build-log/artifacts/next-evidence/packing/lme-s-pilot-{current,cap1200,n12}.json`.
2. **The paid reader-QA is currently unauthorized.** The packet at
   `docs/build-log/artifacts/rung7-packing-reader-gate/authorization-request.json`
   is tombstoned (`authorization:null`), and repo policy
   (`docs/build-log/2026-07-24-packing-technique-screen.md:157`) requires passing
   both frozen free gates first. Phase 0 resolves this for $0 by amending the gate.
3. **SWE-ContextBench is baseline-saturated for us** (first 3 no-memory baselines
   10/10 F2P + 315/315 P2P; the required +2 gain is impossible —
   `docs/build-log/2026-07-24-swe-contextbench-next-evidence.md`). Not a near-term
   instrument.
4. **There is no ANN index to tune.** Vector recall is an exact `<=>` scan
   (`memphant_migrations/versions/20260703_001_wsa_bootstrap.sql:864-866`,
   `crates/memphant-store-postgres/src/store.rs:2504-2514`). pgvector tuning is a
   no-op today.
5. **The coding lane has zero valid golden questions.** The one attempt was
   rejected wholesale (recall 0.0, 19/40 generic templates, distractors
   unadjudicated — `docs/build-log/artifacts/c3-public-code-lane-v3/rejection-receipt.json`).
   This is the single point of failure for every claim about our actual users.

## What users need the memory to do (evidence-ranked)

The CaaS product is users *chatting* with coding agents: follow-ups, corrections,
volunteered facts, preferences, skill-driven work. Three-source investigation
(Tacitry's corrections→rules pipeline read from source; a census of a real power
user's 357-entry agent-memory corpus; 2026 user-voice research) ranks the needs:

1. **Correction retention** — the #1 voiced pain across Claude Code / Cursor /
   Copilot / Windsurf: re-correcting the same mistake 4+ times. PrefEval shows
   preference-following collapses below 10% after ~10 turns without memory — that
   is the published baseline to beat, and no coding-domain benchmark measures it.
2. **Staleness is worse than no memory** — Windsurf's hidden stale auto-memories
   applying dead rules is the strongest pain after forgetting. NOT applying an
   invalidated memory must be a scored win.
3. **Scope-keyed, never global** — measured in the power-user corpus: the same user
   holds opposite standing rules in two repos (worktrees mandatory vs forbidden),
   both correct in scope. Global-preference baselines must fail these probes.
4. **Sycophancy resistance** — wrong user "corrections" must not clobber verified
   repo facts; preference updates must still land; conflicts are surfaced to the
   user, never silently ignored.
5. **Adherence ≠ retrieval** — the sharpest complaint is stored-but-ignored
   ("CLAUDE.md is the least effective way to communicate"). End behavior is the
   metric; retrieval@k is not.
6. **Graduation** — recurring memories should promote to durable standing
   instructions (also live product practice in Copilot Memory). Recorded as a
   direction; no probe design yet, so not scheduled.

Design inputs adopted from Tacitry (verified in its source): the **durability
gate** (decide "would this lesson be correct on a future, unrelated task?" *before*
extracting; non-durable output dropped at the boundary; empty output valid); the
**dual application path** (prose rules in context, mechanically checkable rules
enforced deterministically post-generation — the adherence answer); **scope
specificity ranking** at retrieval (task > repo > org > user-global); **corrections
as full audit rows**; **paraphrase-don't-quote**; a **closed vocabulary of memory
paths**. Rejected from Tacitry: its insert-only rule store (weight frozen at 1.0,
no dedup/supersede/decay) — MemPhant's correct/forget/supersede machinery is
precisely the advantage; do not copy the gap. Regeneration-on-violation, when it
graduates from design note to phase, carries a preregistered violation-rate ×
regen-cost budget with a max-1-regen cap.

The power-user write path to emulate: raw episodes are almost never stored —
everything is distilled at write time into rule + incident-why + how-to-apply with
provenance (source agent, date, confidence, session id), episodic material
surviving as justification attached to the rule. Two authority classes: user-issued
corrections (high authority) vs agent self-learned postmortems (revisable).
Lifecycle entries ("killed, do not retry", dated supersession chains) are
first-class; a substrate without tombstones resurrects rejected work.

## The program

Spend-ordered; each phase has a kill gate; later phases are conditional on earlier
verdicts. Do not parallelize paid phases.

### Phase 0 — $0. Governance amendment (unblocks everything)

Amend the canonical packing decision record
(`docs/build-log/2026-07-24-packing-technique-screen.md` + a dated addendum):
rescind the free exact-abstention sentinel as a decision gate for reader-visible
levers, on the trap-session evidence above; reclassify n≤12 frozen screens as
non-decisional tripwires; record SWE-ContextBench first-tranche saturation as
terminal for the current tranche. Add a dated STATUS.md banner-note addendum (notes
only, no checkbox flips) so the live ledger matches the amended record. Re-issue
the reader-QA packet as **schema_version 3** (the tombstone is already v2) with the
Phase 2 design below.

Kill gate: none — a $0 correction of a measurement instrument, argued from recorded
per-question artifacts.

### Phase 1 — $0. Golden banks + substrate-transfer replay (critical path)

Accounting convention: mining/adjudication runs on subscription-model agent calls —
marginal-$0, not zero-compute.

**1a-R. Repo-memory golden bank (Track R).** From
`nebius/SWE-rebench-openhands-trajectories` (CC-BY-4.0, pinned; 67k trajectories,
verified live) via the proven `materialize_public_code_lane.py` + `code_lane_*`
machinery — mechanism works; only golden mining failed. The new miner must fix the
three recorded failure modes: questions causally identify their target event (no
generic templates), distractors adjudicated, every golden agent-adjudicated with
human spot-checks before freezing. Preregister the quality bar before mining.
**Target: 150–200 goldens** across state-churn / file-symbol-grounding /
task-resumption shapes — sized so Phase 3 can decide ~7pt deltas, the same
resolution as the chat lane, rather than the ~10pt floor a 100-golden bank allows.
Also ingest a smaller internal slice from `syndai.trajectory_events` via the same
adapter.

**1a-U. User-learning golden bank (Track U), first slice.** Goldens for what the
agent must learn about the USER, mined read-only from material we own (C1 extract
pattern): the 60 `feedback_*` memory files across `~/.claude/projects/*/memory/`
(verified count), Syndai `LEARNINGS.md`, and AGENTS.md hard-rule sections (pin
exact counts at extraction). Category weights follow the measured power-user
distribution (n=1 — adopt now, revisit on real CaaS telemetry): ~65% procedural /
~20% semantic / ~10% guardrails-with-exceptions / ~5% identity. Every correction
golden is a bundle (rule + incident + how-to-apply). First slice covers the three
axes scoreable with existing machinery — correction retention, staleness/
invalidation, scope contradiction — target 40–60 goldens. Landing place: new
`scripts/user_lane_extract.py` on the `episodic_lane_corpus.py` pattern, gitignored
bank + committed lock.

Privacy preregistration (before mining): the bank derives from the owner's personal
memory. Bodies are gitignored and never committed; any external claim — including
published correction-retention numbers — requires a paraphrase-scrubbed or
synthetic public-reproducible variant, re-adjudicated to the same bar.

**1b. Substrate-transfer replay ($0, deterministic).** Rerun the Budget-drop
diagnosis (cap=None vs cap=1200) on the ingested code-trajectory corpus using the
Track R goldens as the query set. Landing place: extend `code_lane_run_memphant.py`
to record `dropped_items`/`RecallDropReason` from the recall trace (already in
`openapi/memphant.v1.json`) and admit `MEMPHANT_PACK_RENDER_CAP` into its packing
config. If the per-item-cost pathology does not recur on code bodies,
pack_render_cap is a chat-lane footnote and Phase 2 drops in priority.

**1c. Retrieval probe ($0).** MemPhant packaged runtime vs the BM25 deterministic
control on the new goldens, r@10.

Kill gates: golden mining fails its preregistered bar again → STOP the coding lane
and escalate the dataset question before any paid spend anywhere. MemPhant does not
beat BM25 on retrieval → ownership decision (d) defaults to "Syndai keeps its
tables" until the substrate wins.

### Phase 2 — ~$30–60. Chat-lane pack_render_cap paired reader-QA

Conditional on: Phase 0 landed, AND (Phase 1b shows the pathology recurs on code
bodies OR the chat lane is explicitly valued on its own — the OR branch requires a
one-line decision-register entry naming who valued it and why).

- **Pool:** all 238 current-exposure questions — reuse the two hash-pinned 178-row
  evidence files in `docs/build-log/artifacts/rung7-packing-reader-gate/`, extend
  with the 60 already-burned questions via a free deterministic
  `bench-lme --emit-qa` run per arm. Analyze the frozen-178 subset
  (lattice-comparable) and the full 238 (powered) separately; preregister both.
- **Arms:** baseline vs cap=1200 only. New techniques go through the amended free
  screens first.
- **Reader/judge:** frozen pair — reader `openai/gpt-5.6-terra` (medium), judge
  `anthropic/claude-sonnet-5` rag-supported-v1 prompt v3. This is a **recorded
  override** of the standing Sol-finalist judge designation for this lane: the only
  frozen paired evidence lives on this pair, and switching lattices orphans it.
  Same-lattice discipline holds within the lane, including the confirmation.
- **Primary endpoint:** paired McNemar on answer correctness over the **221 scored
  (non-`_abs`) rows**; d_min = 7pt (powered ~80% at ψ≈0.15; 5pt is undecidable on
  unsealed material — pre-commit to |Δ|<7pt = no flip). Analysis code committed
  before unblinding.
- **Abstention (powered secondary endpoint):** budget upgrade — mint **40–60
  trap-preserving `_abs` variants** from already-exposed base questions, built the
  way LME builds `_abs`: withhold the answer session while **keeping the near-miss
  traps in the haystack**. This is the correct construct ("abstain despite
  plausible distractors"), unlike the rejected gold-ablation probes ("abstain on
  empty evidence"). Adjudicate each variant before freezing. Combined with the 17
  natural unsealed `_abs` cases, abstention becomes a real endpoint
  (reader-judged: `abstain=true ∧ answer=null`) able to detect large deltas —
  directly answering the objection that killed the lever. Net abstention
  regression still blocks promotion regardless of the primary result. (~$10–20 of
  the phase budget.)
- **Latency/cost guardrail (preregistered):** the cap is construction-time-only at
  the identical 8192 budget and lowers reader tokens, so promotion is expected
  latency-neutral — but promotion criteria still re-run the $0 SLO harness
  (p50<200ms / p95<500ms) and record the reader-token delta. Assumed-neutral is
  not preregistered-neutral.
- **Metric naming:** for two-sided behaviors, score both directions by name —
  Misapplication Rate and Appropriate Application Rate (the BenchPreS pair) — so
  suppression wins can't masquerade as application wins.
- **Cost:** ~$0.02–0.03/call observed → ~$30–60 realistic including the `_abs`
  minting (worst-case ceiling ~$175, re-derived for the 238+variants pool).

**Promotion (chat lane only):** a pass triggers (i) the sealed-259 confirmation —
spent exactly once, exposure recorded immediately after; public claims say
"answer-session disjoint", never "fully held out" (strict all-haystack-disjoint
count is 0); and (ii) a **production-representative robustness arm**: replicate the
headline comparison with reader `claude-opus-4-8` — the model Syndai's Claude Code
executor actually serves (`harness_models.py:34`, "workhorse default") — frozen as
its own lattice, direction-agreement bar (not significance). A default that only
wins on the eval-lattice reader is fragile evidence AND unrepresentative of
deployed traffic; single-reader results are the standard critique of vendor evals.
(Confirmation ~$15–30 + robustness ~$20–40, both conditional on a pass.)

### Phase 3 — ~$30–55. Coding-lane paired reader-QA (the decision run for our users)

Three arms on the Phase-1 golden bank — **BM25 control, MemPhant cap-OFF, MemPhant
cap-1200** — same McNemar machinery, d_min preregistered before launch. With budget
pressure off, the third arm measures the coding-lane cap default directly instead
of inferring it from Phase 1b + chat-lane transfer; 1b's replay remains the free
early-warning gate. Primary comparison: best MemPhant arm vs BM25 — the first
measured answer to "does MemPhant memory help a coding agent". Secondary: cap-OFF
vs cap-1200 decides the coding-lane packing default on its own corpus.

**Reader = production-representative by design:** `claude-opus-4-8` — the model
Syndai's Claude Code executor serves as its workhorse default
(`backend/src/features/coding/harness_models.py:34`) — frozen with a pinned
snapshot and a same-lattice judge in the schema_version-3 packet. The decision run
for our users measures the model our users actually get; the chat-lane
terra/sonnet pair stays confined to Phase 2, where it exists only to reuse the
frozen paired evidence. If the executor default changes before launch, re-pin to
the new default at preregistration time and record the change.

Replication: rerun the retrieval probe on the 270-row Syndai C1 prod extract —
$0, corpus-only (C1 has no mined goldens; minting them is out of scope).

Promotion of any default here also passes the same SLO/reader-token guardrail, and
a promotion-grade result gets the same second-reader direction check as Phase 2.

Kill gate: no paired win over BM25 → the substrate has not earned the coding lane;
ownership stays with Syndai's tables and the roadmap pivots to closing the measured
gap, not to migration or benchmarks.

### Phase 4 — conditional, ~$20–45. v5 LME-V2 resume (parked, not dead)

Parked until Phases 1–3 report. LME-V2 is a web-agent instrument
(WebArena/WorkArena trajectories); with weak cross-domain transfer its value is a
neutral-instrument datapoint, not proof for our users. The $27.13 settled already
bought its deliverable (canary 64/64; one-sided 95% upper failure bound 4.573% <
15% gate). The $15.18 "liability" is a retained maximum, not recoverable by
resuming.

If resumed: minimal S0 only (settlement classes + never-redispatch-captured-
response tests + durable per-plan terminal summaries), fresh zero-spend
recensus/re-authorization (retry pool $10.00 < outstanding max liability $15.18 —
must be reconciled first), provider decision in order: drop `allow_fallbacks:false`
if provider identity is not evidence-load-bearing, else BYOK DeepInfra; no bespoke
wave-scheduler subsystem. Never score the 7,352-survivor subset partially. Note for
un-parking: the official leaderboard scores LAFS Gain — an accuracy-LATENCY
frontier over 1–200s budgets — so a submission needs a preregistered latency
budget. SWE-Explore monitor: the upstream README claims issue text is included; the
shipped JSONL (pinned rev bdb0ae4, still latest) has 0/848 — re-verify the data,
never the README.

## Eval axes for Track U

| Axis | Probe shape | Scored win |
|---|---|---|
| Correction retention (slice 1) | correction in session N → temptation to repeat in N+k | mistake not repeated |
| Staleness/invalidation (slice 1) | preference changed / rule retired | old memory NOT applied |
| Scope contradiction (slice 1) | same user, opposite rules in two repos | scope-correct rule retrieved |
| Guardrail exceptions (deferred) | "never X unless explicitly asked" ± explicit ask | exception grammar honored both ways |
| Sycophancy (deferred) | wrong user "correction" vs verified repo fact | fact survives; preference updates land; conflict surfaced to the user |
| Lifecycle (deferred) | superseded + killed entries | newer rule wins; killed work not resurrected |
| Adherence (deferred) | rule stored and retrieved | end behavior complies, not just retrieval@k |

Slice-1 axes run on existing machinery; deferred axes enter only with their own
preregistered end-behavior scorers.

Positioning: users distrust memory vendors for shipping zero measured evidence and
distrust cloud memory on privacy (Cursor removed Memories over it). An
accuracy-first, self-hostable substrate that publishes correction-retention numbers
(from the public-reproducible variant, never the private bank) answers both; the
Always-On Agents survey's "the field scores answers, not state governance" line is
independent corroboration for the staleness axis.

## Open questions — answered

**(a) pack_render_cap:** real retrieval win (Δr@10 +0.2349, two-seed), killed by a
mis-specified $0 proxy with zero power. Fix the gate for $0 (Phase 0), test
transfer for $0 (Phase 1b), decide with one powered paired run per lane (Phases
2–3), with abstention made a powered endpoint via trap-preserving variants.

**(b) Datasets:** mostly not representative yet — LME-S is chat, LME-V2 is
web-agent, our users are coding agents. Requirement → source map:

| Requirement | Source | State |
|---|---|---|
| State churn / latest-state-wins | LME-V2 dynamic/static-state types | v5 parked; partial proxy |
| File/symbol grounding | nebius SWE-rebench via C3 adapter | mechanism proven; goldens = Phase 1a-R |
| Task resumption / episodic continuity | Syndai C1 prod extract (270 rows) | ready, read-only |
| User corrections / preferences / rules | 60 `feedback_*` files + LEARNINGS.md + AGENTS.md (read-only) | ready; Phase 1a-U |
| Repo exploration | SWE-Explore-Bench | blocked upstream; monitor the data |
| Long tool-call trajectories | LME-V2 trajectories (1.2 GB, pinned) | cached |
| Internal schema-native traces | `syndai.trajectory_events` | contract live; local dump wiped |

Principles transfer across substrates is testable, not assumable (Phase 1b for $0;
Phase 2-vs-3 agreement as the cross-lane check). Published evidence says design as
if each lane must earn its own defaults.

**(c) v5 sequencing:** park; resume only after the representativeness verdict, with
minimal S0 (Phase 4).

**(d) Repo-memory ownership:** decided by measurement, not spec. Spec-07's boundary
("MemPhant is standalone; Syndai is a client") stands as direction; zero table
migration until Phase 3 shows a paired win replicated on the C1 slice.
Prerequisites for any authoritative-for-prod cutover: the `memphant_app`
non-superuser served role lands first (today the served path bypasses RLS), and
cutover proceeds read-path-by-read-path behind Syndai's degraded-fallback pattern.
Permanently Syndai-side: persona rendering, behavioral reinforcement, managed
repo-doc reconciliation, turn accounting — orchestration, not memory. Mobile
(Flutter memory hub reading `syndai.*`) is the binding surface; web constrains
nothing.

## Explicitly not doing

- pgvector HNSW/iterative-scan tuning — no ANN index exists; exact scan has no
  post-filter recall loss.
- B3 `memory_20250818` six-command adapter polish — no consumer exists yet; track
  the GA contract, build when one does.
- BYOK + Retry-After wave-scheduler subsystem — one campaign's tail does not
  justify it; KISS options live in Phase 4.
- Letta/OpenHands serialization formats — new formats for a substrate with a
  working schema; matches recorded YAGNI verdicts.
- MemoryCode / SWE-Bench-CL as new instruments — instruments exist; none has
  produced a promotable coding-lane result; adding more is sprawl.
- Sufficiency-autorater co-primary endpoint — a second judged construct in runs
  whose binding constraint is power.
- 7-table Syndai→MemPhant migration sequencing — no migration before measurement.
- Any n≤12 frozen screen as a decision instrument — tripwires only.
- Auto-research / LLM-designed experiments — see Research sufficiency.
- Graduation-pipeline probes and the four deferred Track-U axes — recorded, not
  scheduled; each enters only with its own preregistered scorer.

## Budget

| Item | Realistic | Notes |
|---|---|---|
| Phases 0–1 | $0 | marginal-$0 agent calls for mining/adjudication |
| Phase 2 run | ~$30–60 | incl. ~$10–20 `_abs` variant minting; ceiling ~$175 |
| Phase 2 promotion (conditional) | ~$35–70 | sealed-259 confirmation + second-reader arm |
| Phase 3 run | ~$30–55 | three arms, 150–200 goldens |
| Phase 4 (parked) | ~$20–45 | within the standing $200 campaign ceiling |

Total to a coding-lane verdict with powered abstention: **~$90–160 realistic**.
Every dollar sits behind a free gate or a recorded decision; the discipline is what
makes the results believable, and a larger budget does not relax it.
