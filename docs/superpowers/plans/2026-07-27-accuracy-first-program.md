# Accuracy-First Program — small decisive tests before big runs

Date: 2026-07-27
Status: PROPOSED (supersedes ad-hoc sequencing; does not touch STATUS.md checkboxes)
Method: 7-lens research fan-out (library docs, 2026 papers/blogs, OSS repos, codebase,
tests/datasets, experimental design, platform surfaces) + adversarial verification pass.
Every load-bearing claim below was verified against a repo file or is cited to one.

## Goal restated

Measured accuracy wins for the users we actually have — coding agents / the CaaS
system, evolving into agentic chat over codebases/tasks/docs — proven on neutral
instruments, with light-before-heavy spend and preregistered kill gates. SOTA claims
are downstream evidence, never the objective. Priority: accuracy > cost > perf/latency.

## Research sufficiency verdict

**We do not need more paper or blog research to start.** Every near-term experiment
below is runnable from assets already in the repo or already cached. External research
added exactly two decision-relevant inputs: (1) cross-domain memory transfer to coding
is weak (+3.7% avg, negative transfer from low-level traces — arXiv:2604.14004), which
makes the coding-lane golden bank the critical path rather than more chat-lane wins;
(2) OpenRouter's documented 429/Retry-After/BYOK contract, which only matters if v5
resumes. The only externally blocked asset is SWE-Explore-Bench (public release lacks
issue text + snapshot commits; `benchmarks/manifests/swe_explore.lock.json` fails
closed) — monitor, don't build around it.

## Evidence corrections this plan rests on (verified)

1. **The pack_render_cap "abstention regression" was not an abstention regression.**
   The 3/4→1/4 flip on the n=12 screen is a retrieval-proxy metric: `score_question`
   in `crates/memphant-eval/src/bench_lme.rs` (~line 615) scores an `_abs` case correct
   when `abstained || first_answer_rank.is_none()` — no model ever abstained. Cap 1200
   packs 7–9 items instead of 4–5, so the near-miss trap sessions (cat-Luna, Harajuku…)
   land at rank 1–2 and the proxy flips. The LME gold answers for those sentinels
   *explicitly require the trap content in context* ("You mentioned your cat Luna but
   not your hamster") — a reader needs the trap to abstain correctly. The free gate
   punishes admitting the very evidence the gold answer needs. It is a mis-specified
   proxy, and with 2 discordant pairs (McNemar exact p=0.5) it had zero power.
   Artifacts: `docs/build-log/artifacts/next-evidence/packing/lme-s-pilot-{current,cap1200,n12}.json`.
2. **The paid reader-QA is currently unauthorized.** The packet at
   `docs/build-log/artifacts/rung7-packing-reader-gate/authorization-request.json` is
   tombstoned (`authorization:null`, SUPERSEDED_REJECTED_BY_2026_07_24_FREE_EXACT_ABSTENTION_GATE)
   and repo policy (`docs/build-log/2026-07-24-packing-technique-screen.md:157`) requires
   passing both frozen free gates first. Phase 0 resolves this for $0 by amending the
   gate, not by paying around it.
3. **SWE-ContextBench is baseline-saturated for us** (first 3 no-memory Codex baselines
   graded 10/10 F2P + 315/315 P2P, required +2 gain impossible —
   `docs/build-log/2026-07-24-swe-contextbench-next-evidence.md`). Not a near-term
   instrument; revisiting requires harder-task selection, which is new work.
4. **There is no ANN index to tune.** Vector recall is an exact `<=>` scan over b-tree
   filter indexes (`memphant_migrations/versions/20260703_001_wsa_bootstrap.sql:864-866`,
   `crates/memphant-store-postgres/src/store.rs:2504-2514`). Any pgvector
   HNSW/iterative-scan tuning work is a no-op today and stays out of this plan.
5. **The coding lane has zero valid golden questions.** The one attempt was rejected
   wholesale (recall 0.0, 19/40 generic templates, distractors unadjudicated —
   `docs/build-log/artifacts/c3-public-code-lane-v3/rejection-receipt.json`). This is
   the single point of failure for every claim about our actual users.

## The program

Spend-ordered. Each phase has a kill gate. Later phases are conditional on earlier
verdicts — do not parallelize paid phases.

### Phase 0 — $0. Governance amendment (unblocks everything)

Amend the canonical packing decision record (`docs/build-log/2026-07-24-packing-technique-screen.md`
+ a dated addendum): rescind the free exact-abstention sentinel as a decision gate for
reader-visible levers, on the trap-session evidence above. Reclassify n≤12 frozen
screens as non-decisional tripwires. Record SWE-ContextBench first-tranche saturation
as terminal for the current tranche. Re-issue the reader-QA packet as schema_version 2
with the reconciled design in Phase 2.

Also in Phase 0 (both $0): a dated STATUS.md banner-note addendum (notes only, no
checkbox flips) recording the sentinel's reclassification, so the live ledger does
not contradict the amended decision record; and the re-issued packet is
schema_version **3** (the tombstoned packet is already v2 — keep the supersedes
chain unambiguous).

Kill gate: none — this is a $0 correction of a measurement instrument, argued from
recorded per-question artifacts.

### Phase 1 — $0. Coding golden bank + substrate-transfer replay (critical path)

This phase decides whether the chat-lane lever matters for our users at all, and
builds the asset every coding-lane measurement depends on.

1a. **Rebuild the coding golden bank — two tracks.**

    *Track R (repo memory):* from `nebius/SWE-rebench-openhands-trajectories`
    (CC-BY-4.0, pinned) via the existing `materialize_public_code_lane.py` +
    `code_lane_*` machinery — mechanism is proven; only golden mining failed. The new
    miner must fix the three recorded failure modes: questions must causally identify
    their target event (no path-varied generic templates), distractors must be
    adjudicated, and every golden gets an agent-adjudicated + human-spot-checked pass
    before freezing. Preregister the quality bar before mining. Target: 60–100 goldens
    across state-churn / file-symbol-grounding / task-resumption question shapes.
    Also target `syndai.trajectory_events` (schema-native internal source) via the same
    adapter for a smaller internal slice.

    *Track U (user learning):* goldens for what the agent must learn about the USER —
    corrections, preferences, standing rules, identity facts — mined read-only from
    real material we already own (same pattern as the C1 prod extract): the 60
    `feedback_*` memory files across `~/.claude/projects/*/memory/` (verified count),
    Syndai `LEARNINGS.md` self-corrections, and AGENTS.md hard-rule sections (pin
    exact source counts at extraction time — the ~25/~15 estimates are unverified).
    Category weights follow the measured distribution of a real power user (n=1 —
    adopt now, preregister revisiting once real CaaS telemetry exists): ~65%
    procedural workflow rules/traps, ~20% semantic project/config facts, ~10%
    guardrails with exception clauses, ~5% identity/style. Every correction golden is
    a BUNDLE (rule + triggering incident + how-to-apply), because that is the
    real-world unit — not isolated fact triples.

    **First slice = three axes only** (correction retention, staleness/invalidation,
    scope contradiction) — the ones scoreable with the existing retrieval + reader-QA
    machinery today. The other four axes (guardrail exceptions, sycophancy,
    lifecycle, adherence) need end-behavior scoring that does not exist yet; they are
    preregistered as deferred, not silently in scope. Landing place: new
    `scripts/user_lane_extract.py` on the `episodic_lane_corpus.py` pattern —
    gitignored bank + committed lock. Target: 40–60 first-slice goldens.

    **Privacy preregistration (before mining, $0):** the Track U bank is mined from
    the owner's real personal memory. Bodies are gitignored/private per the C1
    pattern and never committed. Any EXTERNAL claim (including the correction-
    retention numbers the positioning note wants published) requires a
    public-reproducible variant — paraphrase-scrubbed or synthetic clones,
    re-adjudicated to the same quality bar. Paraphrase-don't-quote applies to every
    derived artifact.

1b. **Substrate-transfer replay ($0, deterministic):** rerun the Budget-drop diagnosis
    (cap=None vs cap=1200) on the ingested code-trajectory corpus. This answers
    "do packing principles transfer across substrates" with zero model calls: if the
    per-item-cost pathology (one long body starving the pack) does not recur on code
    bodies, pack_render_cap is a chat-lane footnote and Phase 2 drops in priority.
    Landing place: extend `code_lane_run_memphant.py` to record
    `dropped_items`/`RecallDropReason` from the recall trace (already exposed in
    `openapi/memphant.v1.json`) and admit `MEMPHANT_PACK_RENDER_CAP` into its packing
    config; query set = the Track R goldens from 1a. External prior worth citing in
    the write-up: memory-condensation strategies showed no quality gain for coding
    agents on DiscoveryBench (arXiv 2605.18854) — transfer is the question, not the
    assumption.

1c. **Retrieval probe:** MemPhant packaged runtime vs the BM25 deterministic control
    (`code_lane_run_deterministic.py`) on the new goldens, r@10, free.

Kill gates: if golden mining fails its preregistered quality bar again, STOP the
coding lane and escalate the dataset question before any paid spend anywhere. If
MemPhant does not beat BM25 on retrieval, the ownership decision (d) defaults to
"Syndai keeps its tables" until the substrate wins.

### Phase 2 — ~$10–25. pack_render_cap paired reader-QA (chat lane)

Conditional on: Phase 0 landed, AND (Phase 1b shows the pathology recurs on code
bodies OR the chat lane is explicitly valued on its own — the OR branch requires a
one-line decision-register entry naming who valued it and why, so the free gate is
bypassed on a recorded judgment, not silently).

One reconciled design (the lenses proposed three; this is the pick):
- **Pool:** all 238 current-exposure questions — reuse the two hash-pinned 178-row
  evidence files in `docs/build-log/artifacts/rung7-packing-reader-gate/` and extend
  with the 60 already-burned questions via a free deterministic `bench-lme --emit-qa`
  run per arm. Analyze the frozen-178 subset (lattice-comparable) and the full 238
  (powered) separately; preregister both.
- **Arms:** baseline vs cap=1200 only. No head+tail / adaptive variants — new
  techniques go through the (amended) free screens first.
- **Reader/judge:** frozen lattice pair — reader `openai/gpt-5.6-terra` (medium),
  judge `anthropic/claude-sonnet-5` rag-supported-v1 prompt v3, per the tombstoned
  packet. Same-lattice pairing is mandatory.
- **Primary endpoint:** paired McNemar on answer correctness. Denominator is the
  **221 scored (non-`_abs`) rows** — abstention is a guardrail stratum, not part of
  the powered test. d_min = 7pt (powered ~80% at ψ≈0.15; a 5pt effect is undecidable
  on unsealed material — pre-commit to |Δ|<7pt = no flip). Commit the analysis code
  before unblinding results.
- **Abstention:** fail-closed guardrail, not a powered stratum — reader-judged
  (`abstain=true ∧ answer=null`) on all 17 unsealed `_abs` cases; any net regression
  blocks promotion. No synthetic gold-ablation probes (they test "abstain on empty
  evidence", not "abstain despite traps" — wrong construct).
- **Latency/cost guardrail (preregistered beside the accuracy endpoint):** the cap is
  construction-time-only at the identical 8192 pack budget and lowers reader tokens,
  so promotion is expected latency-neutral — but the promotion criteria still re-run
  the existing $0 SLO harness (p50<200ms / p95<500ms bars) and record the
  reader-token delta as a non-regression check. Assumed-neutral is not preregistered-
  neutral.
- **Lattice reconciliation (recorded override):** Phase 2 and its confirmation run
  end-to-end on the terra/sonnet pair from the frozen packet — an explicit recorded
  override of the standing Sol-finalist judge designation for this lane. Rationale:
  the only existing paired evidence (two frozen 178-row files) was generated on that
  pair; switching lattices orphans it. Same-lattice discipline holds within the lane.
- **Cost basis (re-derived for the 238 pool):** ~$0.02–0.03/call observed →
  **~$18–40 realistic, ~$155 worst-case** (the old $116 ceiling was the 178-row
  derivation). The conditional sealed-259 confirmation run is a separate named
  budget line (~$15–30 realistic) spent only on a pass.
- **Promotion:** a pass flips the default for the CHAT lane only. Coding-lane default
  waits for Phase 3 on its own corpus. The sealed 259 confirmation set is spent
  exactly once, only for a promotion-grade confirmation, and its exposure is recorded
  immediately (append-only invariant enforces this). Caveat recorded in advance:
  strict all-haystack-disjoint count is 0, so public claims say "answer-session
  disjoint", never "fully held out".

### Phase 3 — ~$15–30. Coding-lane paired reader-QA (the decision run for our users)

MemPhant vs BM25-control paired reader-QA on the Phase-1 golden bank, same McNemar
machinery, preregistered d_min before launch. This is the first measured answer to
"does MemPhant memory help a coding agent".

The MemPhant arm's packing config **inherits the Phase 1b verdict** (cap stays OFF
unless 1b showed the code-body pathology). Phase 3 as designed does NOT decide the
coding-lane cap default — that is decided by 1b + Phase 2 transfer reasoning, or by
an explicitly named third arm (+$8–15) if a direct measurement is wanted. The C1
replication is **$0 corpus-only retrieval replication** (rerun the retrieval probe on
the 270-row prod extract), not a paid reader run — C1 has no mined goldens and
minting them is out of scope here.

Kill gate: no paired win over BM25 → the substrate does not yet earn the coding lane;
ownership stays with Syndai's tables and the roadmap pivots to closing the measured
gap, not to migration or benchmarks.

### Phase 4 — conditional, ~$20–45. v5 LME-V2 resume (parked, not dead)

Parked until Phases 1–3 report. LME-V2 is a web-agent instrument (WebArena/WorkArena
trajectories); with weak cross-domain transfer, its value is a neutral-instrument
datapoint, not proof for our users. The $27.13 settled already bought its deliverable
(protocol reliability: canary 64/64, upper bound 4.573% < 15% gate). The $15.18
"liability" is a retained maximum, not recoverable by resuming — resuming to recover
it is loss-aversion.

If resumed: minimal S0 only (settlement classes + never-redispatch-captured-response
tests + durable per-plan terminal summaries), fresh zero-spend recensus/
re-authorization (retry pool $10.00 < outstanding max liability $15.18 — must be
reconciled), and the provider decision in this order: drop `allow_fallbacks:false` if
provider identity is not evidence-load-bearing; else BYOK DeepInfra key (own rate
limits); no bespoke wave-scheduler subsystem for one campaign's tail. Never score the
7,352-survivor subset partially.

## Open questions — answered

**(a) pack_render_cap:** Real retrieval win (Δr@10 +0.2349, two-seed), killed by a
mis-specified $0 proxy with zero power. Fix the gate for $0 (Phase 0), test transfer
for $0 (Phase 1b), then decide with one properly-powered paired run (Phase 2).

**(b) Are we gathering the right datasets? Mostly not yet, and this is the biggest gap.**
LME-S is chat-style personal memory; LME-V2 is web-agent trajectories. Our users are
coding agents. Mapping of what a representative coding-memory suite must contain →
best available source:

| Requirement | Source | State |
|---|---|---|
| State churn / latest-state-wins | LME-V2 dynamic/static-state types | v5 parked; partial proxy |
| File/symbol grounding | nebius SWE-rebench trajectories via C3 adapter | mechanism proven, goldens to rebuild (Phase 1a) |
| Task resumption / episodic continuity | Syndai C1 prod extract (270 rows) | ready, read-only |
| Repo exploration | SWE-Explore-Bench | blocked upstream (missing issue text); monitor |
| Long tool-call trajectories | LME-V2 trajectories (1.2 GB, pinned) | cached |
| Internal schema-native traces | `syndai.trajectory_events` | table contract live; local dump wiped |
| User corrections / preferences / rules | 60 `feedback_*` memory files + LEARNINGS.md + AGENTS.md hard rules (read-only extract) | ready; Track U, Phase 1a |

**Principles transfer across substrates is testable, not assumable** — Phase 1b tests
the packing principle on code bodies for $0; Phase 3 vs Phase 2 tests whether the
reader-QA verdicts agree across lanes. Published evidence says expect weak transfer;
design as if each lane must earn its own defaults.

**(c) v5 sequencing:** Park; resume only after the representativeness verdict, with
minimal S0. See Phase 4.

**(d) Repo-memory ownership (MemPhant vs Syndai):** Decide by measurement, not spec.
The spec-07 boundary ("MemPhant is standalone; Syndai is a client") stands as
*direction*; zero table migration until Phase 3 shows a paired win replicated on the
C1 slice. Prerequisites for any authoritative-for-prod cutover: the `memphant_app`
non-superuser served role lands first (today the served path runs as superuser and
bypasses RLS — a benchmark caveat that must not become a production hole), and the
cutover proceeds read-path-by-read-path behind Syndai's existing degraded-fallback
pattern. Permanently Syndai-side regardless: persona rendering, behavioral
reinforcement, managed repo-doc reconciliation, turn accounting (`agent_runs.turns_used`)
— orchestration state, not memory. Mobile is the binding surface (Flutter memory hub
reads `syndai.*`); web imposes no constraint.

## User-learning axis (CaaS chat scope — added 2026-07-27)

The CaaS product is users *chatting* with coding agents: follow-ups, corrections,
volunteered facts, preferences, skill-driven work (SEO, UI, video). The memory that
matters most to those users is memory of THEM, not only of the repo. Three-source
investigation (Tacitry repo, this power user's own 357-entry agent-memory corpus,
2026 user-voice research) yields the following design inputs and eval axes.
Scope note: this covers the coding/CaaS lane only — Syndai's multi-hierarchical
task/ask/research side is explicitly out of scope here.

### What the evidence says users need (in priority order)

1. **Correction retention** — the #1 user-voiced pain across Claude Code / Cursor /
   Copilot / Windsurf: re-explaining conventions, re-correcting the same mistake 4+
   times. No coding-specific benchmark measures this (MemSyco-Bench / PrefEval /
   PERMA are chat-domain) — **a first-mover eval slot** consistent with the 2026-07
   landscape memo. PrefEval shows preference-following collapses below 10% after ~10
   turns without memory; that is the baseline to beat.
2. **Staleness is worse than no memory** — Windsurf's hidden auto-memories applying
   dead rules/stale URLs is the strongest pain signal after forgetting. Invalidation
   (changed preference, retired rule) must be a *scored* behavior: the win is NOT
   applying the old memory.
3. **Scope-keyed, never global** — measured in this user's own corpus: the same user
   holds opposite standing rules in two repos (worktrees mandatory vs forbidden),
   both correct in scope. Every 2026 product treats repo-scope vs user-global as
   structural. Global-preference baselines must FAIL the contradiction probes.
4. **Sycophancy resistance** — wrong user "corrections" must not clobber verified
   repo facts (MemSyco-Bench axis): preference updates overwrite; fact corruption
   must not.
5. **Adherence ≠ retrieval** — users' sharpest complaint is stored-but-ignored
   instructions ("CLAUDE.md is the least effective way to communicate with it").
   The eval must score end behavior, not retrieval@k.
6. **Graduation** — recurring memories should promote to durable standing
   instructions; measurable as re-learn count before promotion. Matches the
   episodic→semantic direction and what users explicitly request.

### Tacitry: steal / reject (verified against its source)

Steal into the procedural substrate design:
- **The durability gate**: an extraction router must decide `durable: true/false`
  *before* emitting anything ("would this lesson be correct on a future, unrelated
  task, without the original context?"), enforced at the boundary (non-durable drops
  any emitted rule), with empty output explicitly valid. Durable-rate is itself an
  audit signal of over/under-extraction.
- **Dual application path**: prose rules injected into context may or may not be
  honored — mechanically checkable rules (style tells, forbidden patterns) get
  structured `applicability` and are enforced *deterministically* post-generation.
  This is the adherence answer: don't trust the model to obey prose; gate what can
  be gated.
- **Scope specificity ranking** at retrieval (thread > recipient > class > workspace
  ⇒ for us: task > repo > org > user-global), with the more-specific rule beating
  the general one on the same tell.
- **Corrections as full audit rows**: decision, edit diff, what was extracted,
  router rationale, durable verdict — the provenance chain MemPhant's trust model
  already wants.
- **Paraphrase-don't-quote** in extracted rules, so one incident doesn't leak
  verbatim into every future generation.
- **Closed vocabulary of memory paths** (hard-validated file/topic namespace) rather
  than free-form keys.

Reject:
- **Insert-only rules** — Tacitry's biggest gap: weight frozen at 1.0, no dedup, no
  supersede-on-contradiction, no decay, append-only memory files. MemPhant's
  correct/forget/supersede machinery is precisely its advantage; do not copy the gap.
- Its pg_trgm retrieval — MemPhant's retrieval stack is strictly stronger.
- Autonomy-as-user-toggle is fine for Tacitry's send-gating; not a memory concern.

### The real power-user write path (measured, n=357 entries)

Raw episodes are almost never stored. Everything is distilled at write time into
rule + incident-why + how-to-apply with provenance (source agent, date, confidence,
session id) — episodic material survives as *justification attached to the rule*.
Two authority classes with different override rights: user-issued corrections (quote
the user, high authority) vs agent self-learned postmortems (revisable). Negative/
lifecycle entries ("killed, do not retry", dated supersession chains) are first-class
— a substrate without tombstones resurrects rejected work. Two-tier index+detail
(always-loaded one-liners under a size budget; long tail on demand) is what users
converge on manually; the substrate should make it native.

### Eval axes for the Track-U golden bank (each preregistered with its own probes)

| Axis | Probe shape | Scored win |
|---|---|---|
| Correction retention (slice 1) | correction in session N → temptation to repeat in N+k | mistake not repeated |
| Staleness/invalidation (slice 1) | preference changed / rule retired | old memory NOT applied |
| Scope contradiction (slice 1) | same user, opposite rules in two repos | scope-correct rule retrieved |
| Guardrail exceptions (deferred) | "never X unless explicitly asked" ± explicit ask | exception grammar honored both ways |
| Sycophancy (deferred) | wrong user "correction" vs verified repo fact | fact survives; preference updates still land; **conflict surfaced to the user**, never silently ignored |
| Lifecycle (deferred) | superseded + killed entries | newer rule wins; killed work not resurrected |
| Adherence (deferred) | rule stored and retrieved | end behavior complies (not just retrieval@k) |

Slice-1 axes are scoreable with existing retrieval + reader-QA machinery; deferred
axes need end-behavior scoring that must be built and preregistered first. For the
scope-contradiction and guardrail axes, score BOTH sides by name — Misapplication
Rate and Appropriate Application Rate (the BenchPreS metric pair) — so suppression
wins can't masquerade as application wins. Graduation (re-learn-count-before-
promotion) has no probe yet and moves to the not-doing list until one exists.

Positioning note: users explicitly distrust memory vendors for shipping zero measured
evidence, and distrust cloud memory on privacy (Cursor removed Memories over it).
Publishing correction-retention numbers from a self-hostable substrate answers both.

## Explicitly not doing (delete list, with reasons)

- pgvector HNSW/iterative-scan audit — no ANN index exists; exact scan has no
  post-filter recall loss.
- B3 `memory_20250818` six-command adapter polish — no client or eval consumes it yet;
  speculative surface. (Track the GA contract; build when a consumer exists.)
- BYOK + Retry-After wave-scheduler subsystem — one campaign's tail does not justify a
  distributed-retry subsystem; the KISS options are in Phase 4.
- Letta/OpenHands serialization "steals" — two new formats for a substrate with a
  working schema; matches the repo's own YAGNI verdicts.
- MemoryCode / SWE-Bench-CL as new instruments — 7+ instruments already exist and zero
  has produced a promotable coding-lane result; adding more is benchmark sprawl.
- Sufficiency-autorater co-primary endpoint — second judged construct in a run whose
  binding constraint is power.
- 7-table Syndai→MemPhant migration sequencing — no migration before measurement (see d).
- Any n≤12 frozen screen as a decision instrument — tripwires only, preregistered as
  non-decisional.
- Auto-research / LLM-designed experiments for these runs — at our scale (2–3 paired
  runs, $10–60, one-shot sealed sets) the scarce resources are question exposure and
  evidence credibility, not design bandwidth; best current systems hit ~45% on
  ablation planning (AblationBench v3). Keep the automation we already use where it
  pays: agent-adjudicated mining with human spot-checks, deterministic replays,
  frozen same-lattice judging.
- Graduation-pipeline probes (re-learn count before promotion) — until an
  end-behavior probe design exists; the axis is recorded, not scheduled.
- The four deferred Track-U axes as Phase-1 scope — first slice is three axes;
  deferred axes enter only with their own preregistered scorers.

## Budget picture

Phases 0–1: $0 (accounting convention: golden mining/adjudication runs on
subscription-model agent calls — marginal-$0, not zero-compute). Phase 2: ~$18–40
(ceiling ~$155, re-derived for the 238 pool), plus a named conditional line ~$15–30
for the sealed-259 confirmation spent only on a pass. Phase 3: ~$15–30 (+$8–15 only
if a third cap arm is explicitly added). Phase 4 if resumed: ~$20–45 within the
standing $200 campaign ceiling. Total new spend to a coding-lane verdict:
**~$35–70 realistic**, every dollar behind a free gate or a recorded decision.

Phase 4 note for whenever it unparks: the official LME-V2 leaderboard scores LAFS
Gain — an accuracy-LATENCY frontier over 1–200s budgets — so a submission needs a
preregistered latency budget, not just accuracy. And the SWE-Explore monitor entry:
the upstream README claims issue text is included; the shipped JSONL (pinned rev
bdb0ae4, still latest) has 0/848 — re-verify the data, not the README.
