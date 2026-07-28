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
    `feedback_*` memory files across `~/.claude/projects/*/memory/` (ready-made
    user-correction goldens), Syndai `LEARNINGS.md` (25 provenance-tagged
    self-corrections), and AGENTS.md hard-rule sections (~15 guardrails). Category
    weights follow the measured distribution of a real power user, not intuition:
    ~65% procedural workflow rules/traps, ~20% semantic project/config facts, ~10%
    guardrails with exception clauses, ~5% identity/style. Every correction golden is
    a BUNDLE (rule + triggering incident + how-to-apply), because that is the
    real-world unit — not isolated fact triples. See "User-learning axis" below for
    the eval axes these goldens must cover.

1b. **Substrate-transfer replay ($0, deterministic):** rerun the Budget-drop diagnosis
    (cap=None vs cap=1200) on the ingested code-trajectory corpus. This answers
    "do packing principles transfer across substrates" with zero model calls: if the
    per-item-cost pathology (one long body starving the pack) does not recur on code
    bodies, pack_render_cap is a chat-lane footnote and Phase 2 drops in priority.

1c. **Retrieval probe:** MemPhant packaged runtime vs the BM25 deterministic control
    (`code_lane_run_deterministic.py`) on the new goldens, r@10, free.

Kill gates: if golden mining fails its preregistered quality bar again, STOP the
coding lane and escalate the dataset question before any paid spend anywhere. If
MemPhant does not beat BM25 on retrieval, the ownership decision (d) defaults to
"Syndai keeps its tables" until the substrate wins.

### Phase 2 — ~$10–25. pack_render_cap paired reader-QA (chat lane)

Conditional on: Phase 0 landed, AND (Phase 1b shows the pathology recurs on code
bodies OR the chat lane is explicitly valued on its own).

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
- **Primary endpoint:** paired McNemar on answer correctness, d_min = 7pt at n=238
  (powered ~80% at ψ≈0.15; a 5pt effect is undecidable on unsealed material —
  pre-commit to |Δ|<7pt = no flip).
- **Abstention:** fail-closed guardrail, not a powered stratum — reader-judged
  (`abstain=true ∧ answer=null`) on all 17 unsealed `_abs` cases; any net regression
  blocks promotion. No synthetic gold-ablation probes (they test "abstain on empty
  evidence", not "abstain despite traps" — wrong construct).
- **Cost basis:** ~$0.02–0.03/call observed → ~$10–25 realistic, $116 worst-case
  ceiling per the frozen derivation.
- **Promotion:** a pass flips the default for the CHAT lane only. Coding-lane default
  waits for Phase 3 on its own corpus. The sealed 259 confirmation set is spent
  exactly once, only for a promotion-grade confirmation, and its exposure is recorded
  immediately (append-only invariant enforces this). Caveat recorded in advance:
  strict all-haystack-disjoint count is 0, so public claims say "answer-session
  disjoint", never "fully held out".

### Phase 3 — ~$15–30. Coding-lane paired reader-QA (the decision run for our users)

MemPhant vs BM25-control paired reader-QA on the Phase-1 golden bank, same McNemar
machinery, preregistered d_min before launch. This is the first measured answer to
"does MemPhant memory help a coding agent". Replicate the headline result on the
270-row Syndai C1 prod extract before acting on it.

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
| Correction retention | correction in session N → temptation to repeat in N+k | mistake not repeated |
| Staleness/invalidation | preference changed / rule retired | old memory NOT applied |
| Scope contradiction | same user, opposite rules in two repos | scope-correct rule retrieved |
| Guardrail exceptions | "never X unless explicitly asked" ± explicit ask | exception grammar honored both ways |
| Sycophancy | wrong user "correction" vs verified repo fact | fact survives; preference updates still land |
| Lifecycle | superseded + killed entries | newer rule wins; killed work not resurrected |
| Adherence | rule stored and retrieved | end behavior complies (not just retrieval@k) |

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

## Budget picture

Phases 0–1: $0. Phase 2: ~$10–25 (ceiling $116). Phase 3: ~$15–30. Phase 4 if
resumed: ~$20–45 within the standing $200 campaign ceiling. Total new spend to a
coding-lane verdict: **under $60 realistic**, every dollar behind a free gate.
