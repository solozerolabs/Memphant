# Session handoff — `memphant capture` design → census kill → parked (2026-08-09)

## TL;DR
Designed `memphant capture` (twice eng-reviewed, owner-approved, tri-domain), then ran its own $0 kill-gate first. **The census killed the outcome-gated capture line on data-availability grounds** — Syndai's coding runs are autonomous (no human corrections) and local Claude Code sessions have no validated outcomes, so no source has *both* axes the mechanism needs. Every pivot (learnings, failure-patterns, MCP redirect) was evaluated and found cornered or already-done. **The line is parked, not replaced** — the disciplined call, per accuracy > cost > speed. Nothing is mid-build; the tree is clean.

## What happened, in order
1. **khive audit** (`kentcdodds`… no — `ohdearquant/khive`) → memory `khive-audit-verdict`. Takeaway that seeded everything: the session-mirror capture idea + single-tool MCP pattern.
2. **`memphant capture` spec** authored from a 9-team parallel analysis → `docs/superpowers/specs/2026-08-09-memphant-capture-design.md`. Core idea: **outcome-gated capture** — a deterministic nominator only *nominates* candidates (`UnitState::Candidate`, inert); the outcome-coupled ledger *labels* them (promote only when a distinct later same-scope task passes with the candidate served). Mask at the edge, nominate server-side. Zero new schema for episodes/chat/outcomes.
3. **Two engineering reviews** folded in (both `plan-eng-review`): fixed a serving deadlock (Stage-1 offline-only; live promotion → Stage 2 on the randomized-counterfactual lane), corrected 3 spec-vs-code errors (`state='captured'` was deleted → use `Candidate`; `regex` isn't a core dep → mask in CLI; agent refs must be scope-namespaced + L0), added binding self-verification, and honest staging. Owner decisions: OSS binding = pre-provisioned + scoped key; nomination server-side.
4. **Tri-domain integration** (3 more teams): the engine is domain-agnostic; chats capture via Syndai's `runner_post_turn` hook-push (no tailable artifact), docs via the resource lane (Stage 1c, the one place "zero schema" breaks — no dedup/valid-time/supersession), Syndai coding-lane serving KILL honored.
5. **Gates plan** → `docs/superpowers/plans/2026-08-09-memphant-capture-gates.md` (Stage 0-pre migration, Stage 1a census, Stage 0 ledger wire). Deliberately ended at a go/no-go.
6. **Ran Part B (census) subagent-driven.** Preflight found the data source (`coding_execution_attempt_events`) wasn't local; created a **linked Syndai worktree** and traced the DB setup (docker-compose supabase/pg17 on :55432 + alembic + `bootstrap_coding_local_db.py`); the real populated data is Syndai **dev** (Doppler `syndai/dev`, `syndai` schema). B1 mined it read-only.
7. **Census verdict: NO-GO(dataset)** → memory `memphant-capture-census-nogo`. See below.
8. **kody audit** (`kentcdodds/kody`) → memory `kody-mcp-takeaways`. MCP patterns mostly already present in `memphant-mcp` (idempotency keys, annotations). No compelling redirect.

## The kill, precisely
- Mined **317 attempts / 183 runs / 6 repos** from Syndai dev, clean outcomes (completed 109 / not-passed 208), privacy held (raw gitignored, counts-only committed).
- Of **1,434 `event_type='user'` rows, 1,430 are the agentic loop's own `tool_result` echoes**; the 4 real-text turns are compaction summaries / skill dumps. **Zero human corrections.** Independently verified.
- Cause: Syndai CaaS is autonomous (no human-in-the-loop mid-run).
- **Load-bearing constraint:** outcome-gated *correction* capture needs a source with BOTH human corrections AND validated outcomes. Neither exists (Syndai = outcomes/no-corrections; local Claude Code = corrections/no-outcomes).
- Pivots evaluated and rejected: **learnings** (cornered by two settled negatives — grep beats us 96.67 vs 58.89 on repo-recoverable facts; injection measured flat +0.9pp); **failure-pattern/adherence** (observational not causal; adherence veto already dead); **MCP redirect** (`memphant-mcp` already has idempotency keys + read/write annotations — `lib.rs:316-432`).

## Decisions (owner-ratified or my call, all recorded)
- Outcome-gated capture: **staged, gated** (spec). Stage-1 offline-only; Stage-2 serving lane behind Phase A ≥0.40.
- Serving deadlock → offline-only Stage 1 (owner).
- OSS binding → pre-provisioned + scoped key (owner).
- Nomination → server-side; mask at the edge (my call, review 2).
- accuracy > cost > speed (owner directive, threaded through the spec).
- **Post-census: park the capture line, do not manufacture a replacement** (my call). Don't ship Stage 0 alone = orphan telemetry / shadow-mode trap.

## The one open decision (owner only)
Outcome-gated capture is testable only with a **both-axes data source** (human corrections + validated outcomes). Creating one is a product call: e.g. an interactive coding lane where the human steers *and* the server validates, or wiring local Claude Code sessions to post task-outcomes. Until then, the capture engine has nothing to prove itself on.

## State / artifacts
- **Commits (branch `codex/outcome-coupled-evolution`):** spec + reviews + tri-domain (`4bcba937`, `45acf780`, `d3c054ed`, `b213c738`, `1446dcd9`, `4ceb7bdc`); gates plan (`76ffb78e`, `07702f80`); census B1 script (`9ef7a68c`). Tree clean.
- **Reusable:** `scripts/capture_census_dataset.py` (read-only dev mine, 7 tests, PGOPTIONS read-only-txn, handles the CC-transcript payload envelope). `benchmarks/data/capture_census.stats.json` (counts only).
- **SDD ledger:** `.superpowers/sdd/2026-08-09-memphant-capture-gates/progress.md` (full census trail).
- **Memory:** `memphant-capture-design`, `memphant-capture-census-nogo`, `kody-mcp-takeaways`, `khive-audit-verdict`, `sid-priority-accuracy-cost-speed`.
- **Migration 010** (`task_outcome` ledger) verified scratch-clean, **still pending on Finn** — human-gated (off-peak, `memphant.*` only).

## Loose ends / cleanup
- **Linked Syndai worktree** `/Users/sidsharma/Syndai-capture-census` (branch `capture/census-db`) — nothing committed there; remove with `git -C /Users/sidsharma/Syndai worktree remove /Users/sidsharma/Syndai-capture-census` when done.
- **Syndai spec mirror**: `STATUS.md`/`30`/`31` were copied into `../Syndai/docs/superpowers/specs/memphant/` (to clear spec-drift) and are **uncommitted** in the Syndai tree — commit there or discard, owner's call (separate repo).
- **Scratchpad clones** (khive, kody) are ephemeral — nothing vendored.
- `benchmarks/data/capture_census.jsonl` + `capture_census_labels.jsonl` are gitignored (carry transcript text) — safe to delete.

## Next-session initial prompt (paste to seed)
> Resuming MemPhant. Last session (see `docs/build-log/2026-08-09-capture-session-handoff.md` and memory `memphant-capture-census-nogo`): the `memphant capture` design is complete and twice-reviewed, but its Stage-1a census returned **NO-GO(dataset)** — outcome-gated *correction* capture is untestable because no data source has both human corrections and validated outcomes. The capture line is **parked**, not replaced.
>
> Do NOT re-run the census or rebuild the capture engine on current data. The only thing that unblocks it is a **both-axes data source** (corrections + validated outcomes) — a product decision I'll make.
>
> Today I want to work on: **[FILL IN]**. Options I'm weighing: (a) decide/spec the both-axes source (interactive-with-validation lane, or wire local Claude Code sessions to post task-outcomes) so capture becomes testable; (b) something unrelated to capture entirely. Read the handoff doc first, then propose the sharpest next step for what I pick — authoritative, KISS, accuracy > cost > speed.
