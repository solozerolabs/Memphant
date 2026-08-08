# MemPhant — The Adherence Plan

Date: 2026-08-05
Status: **PLAN OF RECORD.** Supersedes the sequencing in `2026-07-31-one-plan.md`
(which remains valid as an evidence record; its §5 standing rules — instrument-
acquisition gate, lineage, mechanism-liveness, prereg discipline — carry forward
unchanged and bind this plan).
Method: one day of instrument deaths ($0), then eleven parallel read-only
analysis teams (official docs, 2026 web landscape, OSS survey, MemPhant
codebase, tests, experimental structures, devil's advocate, Syndai backend,
database census, web surfaces, mobile), synthesized and adjudicated here.
Pre-production, zero users: anything may be deleted or rewritten.
Priorities: **UX > accuracy > cost > latency > security-of-our-own-surface.**

---

## 1. The thesis, and the evidence that forces it

**MemPhant is no longer a retrieval product. It is the memory-backed adherence
and repo-profile layer for coding agents.**

Four measurements, three of them ours, one external, all pointing the same way:

| finding | source |
|---|---|
| grep 96.67% vs MemPhant 58.89% on repo-recoverable facts | S4, n=180, p=1.2e-19 |
| BM25 (~40 lines) saturates a real 410-unit memory corpus at 0.9091, flat across overlap strata | XS acquisition gate |
| ~75% of what agents lacked was already written down; corrections in 32% of sessions are violations of in-context rules; violations occur both deep AND shallow (33% in first 30% of session) | transcript mining n≈50 + depth check n=6 |
| Hooks achieve 98% routing compliance where instruction files achieve 60% | context-mode (19.6k★), corroborating |

And the demand-side confirmations nobody planned: 47% of real prod coding
missions carry explicit scope-constraint language; the omission/commission
asymmetry (arXiv 2604.20911: *don't-do rules decay over context, do-do rules
persist*) independently predicts exactly the violations we observed; and Sid
asked for the per-repo profile in his own words on 07-26 before any of this was
measured.

**The market white space is specific and currently empty**: everyone building
guardrails (Earthly Lunar, ActPlane) assumes a human hand-authors the policies;
everyone building memory (Mem0, Letta, Zep, Graphiti) captures via tool calls
the model forgets to make and ignores enforcement entirely. Nobody mines the
rules from the agent's own correction traffic, maintains them per-repo, and
enforces them at action time. That intersection is the product.

## 2. What we already have (the pivot is cheaper than it looks)

The codebase audit corrected this plan's own working assumption. Built and
strict-contract-wired today:

- **The MCP server** — 7 tools over stdio + streamable-HTTP, bind_context on
  every verb, plus the `memory_20250818` file-memory adapter already rendering
  a bounded `/memories/MEMORY.md` in Claude Code's auto-memory convention. The
  always-on block is a **rendering change**, not a subsystem.
- **The `Preference` kind** — "declared, never promoted; superseded or revoked,
  never decayed" (`20260731_006`). This *is* the standing-rule type.
- **The standing-rules query, 90%** — `GET /v1/scopes/{id}/memory` +
  projection/compile-to-disk; missing only a kind filter.
- **Per-repo and cross-repo scope** — `scope.external_ref` + ltree +
  `scope_policy` enforced on the read path. A repo is a scope row. Cross-repo
  within one subject is existing schema; cross-*user* sharing is new.
- **Ingestion** — `retain` takes free-form episodes; spec 07 §7.1 already wrote
  the Hook→Retain cookbook; spec 08 §5.1b already concluded capture "cannot be
  delivered by a tool the model must choose to invoke."
- **The architecture fit is exact**: deterministic hook capture on the write
  path + async LLM mining in `reflect` stage 1 is *literally RW-2*. The OSS
  survey's "steal" list (deterministic capture, no LLM at write time) and this
  codebase's oldest invariant are the same design. Graphiti hangs on
  correction episodes precisely because it put the LLM in the write path.

Hard gaps: the pinned block was **deliberately deleted** (migration
`20260801_009`, executing a recorded rejection) — rebuilding it reverses a
register decision, is done openly, and is gated by 04 §12's own restraint
measurement. The W6 miner extracts first-person chat preferences, not
corrections from coding transcripts — the correction/trap mining pass is the
one genuinely new extraction surface. Procedural `Validated` has no write path,
so enforcement cannot lean on procedural validation yet.

## 3. Product structure (the call)

**Spine**: memphant-server as it exists — one store, bind_context, scopes,
Preference/episodic kinds, reflect. Not a new daemon; the "sidecar" of the
experimental team's winning shape is the server we already ship, reached over
localhost or hosted.

**Delivery is per-harness shims, thin by design** (the docs team's negative
space dictates this — MCP is pull-only everywhere, veto exists only in Claude
Code and Pi):

1. **Syndai** (shim #1, first, deepest): adapter verb extensions + enhancer
   join-part + finalize capture + author_command_guard rules. Full in-loop
   fidelity; best capture; measurable on existing run metadata.
2. **Claude Code plugin** (shim #2, the external product): one install ships
   SessionStart inject (compiled block) + PreToolUse enforcement (warn-mode
   default) + SessionEnd capture (transcript_path → retain) + the MCP recall
   tool + auto-memory MEMORY.md. All five surfaces are documented and
   supported.
3. **Pi extension** (shim #3): TS lifecycle hooks give all three needs
   in-process.
4. **Codex/aider**: injection-only degradation (AGENTS.md / conventions file
   compiled from the same store). No veto surface exists there; we do not
   pretend otherwise.

**MCP's role is demoted to what the evidence supports**: the portability layer
for on-demand recall and nothing else. It is never the spine — a pull protocol
cannot deliver any of the three needs that matter.

**Surface shape: one strong recall tool listed by default, the other six verbs
unlisted** (env-gated re-enable, e.g. `MEMPHANT_MCP_TOOLS`). Since MCP is
recall-compat only, the agent's job at the surface is one thing — recall — and a
menu of seven verbs invites mis-picks and burns tool-schema context every
session. Steal from CodeGraph (evaluated 2026-08-08, `codegraph_explore`): they
measured one strong tool steering agents better than a narrow-tool menu. The
bind_context handshake and all seven verbs stay fully functional and reachable;
this is a default-listing change to the MCP surface, not a capability cut.

**`memphant init` is onboarding, not architecture**: mints the repo scope,
installs the plugin/hooks, compiles the current block to files. The
compile-to-flat-files layer (experimental structure B) lives *inside* this
shape as the zero-latency baseline and the team-distribution channel (a PR a
human reviews), not as the product.

**Capture is deterministic, always**: hook-lifecycle events (edits, commands,
errors, user corrections, vetoes, overrides) written via `retain` with no LLM
and no model cooperation. All LLM judgment lives in async `reflect`. The
enforcement loop feeds the memory: a veto the user overrides marks a wrong
rule (supersession candidate); a correction with no veto marks a missing rule
(mining candidate). **Capture and enforcement are one loop — structures that
separate them starve their own memory.**

## 4. Latency / performance / cost (the call)

- **Enforcement hot path: no LLM, no network, <100ms** — pre-compiled
  local rules (regex/AST predicates over tool_input), shipped as part of the
  compiled block. This is non-negotiable; it is also what makes cost ~zero.
- **Injection: session-start only**, bounded (≤4KB compiled block). Mid-session
  re-injection only on the omission-decay schedule (re-assert don't-rules
  after N tool calls — the one place the research licenses spending tokens).
- **Capture: async, fire-and-forget, fail-open** — copying Syndai's
  transcript-retention contract (bounded, never-raising).
- **Mining/reflect: batch, off the serve path** — where LLM cost already
  lives. No paid call on any user-facing path, ever.

## 5. UX (the call that outranks the others)

- **Zero-config after install.** One command; nothing to remember to invoke.
  The product works when the user forgets it exists — the measured failure of
  every tool-call-based memory product is that the model forgets to call it.
- **Warn-mode is the default; veto is earned per-rule** by measured precision
  (G1 below). A false-positive veto is the uninstall event; a warning is not.
- **Every intervention is visible and attributable**: "rule X, learned from
  session Y, fired here." In Syndai: a subsection of the existing run-detail
  "resolved evidence" disclosure + a fired-rules card (both ride the existing
  status poll). Never a settings graveyard — the portal audit shows the memory
  UX graveyard risk is real (7 of 14 memory tables never written, proxy walls
  off the API, three fact-proposals stuck forever because no confirm surface
  was ever built).
- **One-click silence** on any rule — which is a supersession event, captured,
  so even rejection trains the store. Mobile inherits the existing
  proposed-fact confirm flow later; nothing mobile-new.

## 6. The gates before the build (devil's advocate, standing discipline)

Three attacks survived scrutiny and are now **preconditions**:

- **G1 — veto-precision instrument ($0, first).** Label tool calls in the ~50
  existing transcripts against the written rules; run candidate trap rules
  offline; report precision/recall per rule. **Kill bar: no rule subset
  reaches ≥95% precision ⇒ the product ships warn-only.** No hook is written
  before this exists. This is also the first deposit on the adherence bank.
- **G2 — external validation of the miss-distribution.** The n=1 risk is real:
  the 75%/32% numbers may be a power-user self-portrait. Re-run the
  classification rubric blind on non-Sid transcripts (friendly users;
  public trajectory sets have no real user corrections, so this needs real
  outsiders). **If "already-written" drops below ~40%, the adherence pillar
  demotes to a Syndai feature and the plan reverts to instrument-hunting.**
- **G3 — model-generation replay.** Replay violation contexts against the
  newest models; if violations vanish, the niche is evaporating and this is a
  short-horizon feature, not a company thesis. Re-run quarterly — this gate
  never closes permanently. (Current evidence says the niche is durable:
  HANDBOOK.md's best frontier score is 36.2%, and omission-decay is
  architectural, not a training gap. But that is a prior, not a measurement.)

Plus the standing instrument rule applied to the new lane's own bench:
**HANDBOOK.md (arXiv 2607.25398) gets the $0 shape census before anything is
claimed on it.** It is the first public instrument this program has ever had a
first-mover shot at; it is also exactly the kind of gift horse the acquisition
gate exists to look in the mouth.

## 7. Sequenced plan

**Phase 0 — gates, $0–20, days.** G1, G2, G3, HANDBOOK.md census. Also fold
the XS/SC tooling into `benchmarks/`: the correction events already mined are
the seed of the adherence bank (they carry the external label every rejected
corpus lacked — the user's "no").

**Phase A — Syndai slice, ~week, behind flags.** The backend team's cheapest
end-to-end slice, built exactly as mapped: `profile-for-repo` +
`rules-for-scope` verbs (adapter +3 functions each, OpenAPI entry, pinned-sha
bump per the tests team's path); profile block as one bounded join-part in the
enhancer's `guidance_block` (verbatim path, not LLM-paraphrased); session-end
capture hook in finalize reading the retained transcript (bounded, fail-open);
profile persisted beside `CodingProject.detected_stack`/`validators_config`
where it naturally belongs; enforcement via author_command_guard rule wrappers.
**Measured on existing run metadata: repair-turn count, EMPTY_DIFF repairs,
attempt cost, flag-on vs flag-off cohorts. Kill criterion preregistered before
the flag flips.** The injection here is a deterministic compiled profile, not
retrieved memory — the SWE-ContextBench Table-4 constraint (injected memory
below no-context) applies to retrieval injection and is why recall stays out of
the coding prompt entirely.

**Phase B — Claude Code plugin, ~2 weeks.** The external product: hooks + MCP
recall + auto-memory rendering, dogfooded on Memphant and Syndai development
itself (which suffers the measured pain: the playwright fact was re-derived 5×
in this very repo). Correction/trap mining pass as a reflect stage-1 extraction
(the W6 gap). Cross-repo scope via the existing same-subject `scope_policy`.

**Phase C — the loop closes.** Veto/override/silence events feed supersession
on `Preference` units (the flag's machinery finally has live traffic —
if T2-style semantic subject identity earns its keep here, it ships on
evidence; if not, it comes out, per the SC lane's standing consequence).
Pinned-block rebuild happens here, openly reversing register decision 009,
gated by the 04 §12 restraint measurement. Portal transparency panel lands
with Phase A's payload.

**Phase D — the public instrument.** HANDBOOK.md run (if its census passes) +
publication of the adherence bank methodology. The neutral-benchmark vacuum
(every memory number in the landscape is vendor-self-run) is a positioning
asset for whoever shows up with a clean instrument first.

**Deletions (proposed now, executed after Phase A proves the direction):**
hosted rerank/embed seams (`api_reranking.rs`, `api_embeddings.rs`), Deep
recall loop (A1: diagnostic-only), `pack_render_cap` + spec-30 merged blocks
(default-OFF, unmeasured), structured-state census CLI, and the retrieval-SOTA
share of `memphant-eval` (~6k LOC) plus its benchmark payloads. Kept: the
recall verb and the core ranking stack that serves it (load-bearing, just no
longer the investment thesis), the eval harness patterns (they killed four bad
lanes in one day — that discipline is the program's proven asset), all
bitemporal/supersession machinery (Phase C is its first real customer).

## 8. Open questions — answered

- **MCP vs skill vs CLI vs API?** All of them, in their measured roles: server
  is the spine, hooks are the product, MCP is recall-compat, CLI is
  onboarding, skills are nothing (on-demand by design — wrong shape for all
  three needs).
- **04 §13.7 OPEN-1 (where compaction runs)?** Moot for now: `working_state`
  stays unminted — this pivot *confirms* the harness owns working memory. If
  it is ever minted, the default is reflect stage 1 by architecture fit
  (RW-2), pending the paired measurement the spec demands.
- **Subject-resolution flag?** Stays off until Phase C gives it the live
  supersession traffic no corpus could (the SC lane's conclusion stands:
  no instrument, no ship — but Phase C *builds* the instrument).
- **Working set for non-Syndai agents?** Claude Code first (only host with all
  three surfaces), Pi second (Syndai's own adapter), Codex/aider
  injection-only. No pretending enforcement exists where the harness has no
  seam.
- **The RLS finding** (180 syndai tables exposed with RLS off, every memory
  table included) is Syndai's decision, not this plan's — flagged to the owner,
  not silently fixed.

## 9. What would kill this plan

Stated now, so nobody moves the bar later: G1 fails (no high-precision rule
subset exists) **and** warn-only shows no cohort effect in Phase A ⇒ adherence
is not enforceable value. G2 fails ⇒ it was a self-portrait; revert to
Syndai-internal feature. Phase A cohort metrics flat with kill criterion met ⇒
the profile pillar demotes to plain checked-in files and the program returns
to the drawing board with its evidence intact. The discipline that killed four
lanes in one day for $0 is the same discipline this plan submits to.
