# What coding agents actually need — the product call, grounded in our own traffic

**Date:** 2026-08-05 · **Branch:** `xsession-controls` · **Spend: $0.**
Evidence: three parallel read-only analyses over (1) 58 Claude Code transcript
files → ~50 distinct sessions, Jul 25–Aug 5; (2) the Syndai coding-mission
prompt-assembly path; (3) a 125/410 stratified sample of the flat-file memory
corpus. Plus the C1 prod extract (292 real coding-mission requests).

Written because four instruments died in one day and the remaining question was
not "which corpus next" but "is the thesis right".

## 0. The finding, in one paragraph

**Retrieval is not the binding constraint, and we now have three independent
measurements saying so.** S4: an agent with `grep` beats MemPhant 96.67% vs
58.89% on repo-recoverable facts. XS: a 40-line BM25 rule saturates a real
410-unit memory corpus at 0.909. And now the demand side: of ~55 concrete
incidents where a coding agent lacked something, **~75% of the missing knowledge
was already written down — much of it in a file that was in the agent's context
at that moment.** The gap is **adherence**, not recall. We have been building a
better index for a shelf the agent already had open.

This is not a new idea in this program; it is `2026-07-31-one-plan.md` §2 item 6
("Correction retention is enforcement, not retrieval… our LSW metric is
wrong-shaped") confirmed on our own traffic four days later.

## 1. Demand — what agents actually lacked (n≈50 sessions, 16 with corrections)

User corrections appear in **32% of sampled sessions**, and their dominant shape
is not "you got a fact wrong" but **"you violated a rule that is already written
down."** The same rule was restated by the user across three different sessions:

- 08-04: *"Why do we do full re-runs? I thought we had documentation to NEVER do
  that."* → the rule is at `Syndai/AGENTS.md:44`, in an **auto-loaded** file.
- 08-05, next day, different session: *"I thought we never run full gate locally
  anymore?"* → same rule, same file.
- 08-01: *"Why do you keep stopping?"* … *"Can you fix yourself to stop stopping
  each round?"* → third restatement of a `SESSION_POLICY.md` rule.

**A memory system that surfaced these facts would have changed nothing. The fact
was on screen.** Split across all incidents: **(a) in the repo ≈45%, (b) in a
flat-file memory doc ≈30%, (c) nowhere ≈25%.**

Corroborating shape from production: of 292 real Syndai coding-mission requests,
**47% carry explicit constraint language** ("make no other changes", "only", "do
not modify"), median request 483 chars. Nearly half of real coding work is
scope-bounding — an adherence problem by construction.

**Re-derivation is weak** (this user already suppresses it with flat files), with
one clean exception that matters: the Yarn-Berry/`playwright: command not found`
diagnosis was independently re-derived in **6 sessions**, and **5 of those were
in the Memphant repo, which has no `LEARNINGS.md` at all**. The fact existed — in
*another project's* ledger. Nobody greps a sibling repo.

## 2. Supply — what a Syndai coding mission actually receives

**The lane that writes the code receives no memory at all.**
`ClaudeCodeCodingRunner.run(self, user_message, **_kwargs)`
(`coding/engine_loop/coding_claude_runner.py:136-142`) swallows every memory
kwarg. `runner_run_kwargs` (`coding/engine_loop/coding_loop_support.py:109-140`)
builds all seven `expanded_*` layers and they are **discarded**. Zero consumers
of `expanded_file_memories` / `expanded_failure_patterns` / `memory_context`
anywhere under `backend/src/features/coding/`. The 7-layer memory system applies
only to plan/review/research nodes; the implementation lane — the one with
`cost_multiplier 8.0` — gets none of it.

Four supporting defects, each independently load-bearing:

1. **Coding budgets over-subscribe by 45%** — per-layer sum 3,620 against
   `total_max` 2,500 (`missions/modes.py:202`, `memory/context_loader_types.py:63`).
2. **Eviction order inverts coding intent** — `failure_patterns:0, trajectory:1,
   file_memory:2` (`memory/context_loader_helpers.py:122-129`), so the three
   coding-specific layers are **evicted first**, and raising their budgets makes
   them both larger and first-dropped.
3. **`failure_hints` carry no coding signal** — content is
   `f"{tool_name}:{error_code}"` (`missions/trajectory.py:96-104`). No compile
   error, test failure, or repair-loop outcome ever writes one.
4. **`file_memory` has no writer in any coding path** — sole production writer is
   the REST controller (`memory/file_memory_controller.py:129`).

And repo guidance is undelivered where it matters: **pi — the default CaaS
adapter — never sees the customer worktree's `AGENTS.md`** (its resource loader
is constructed with `cwd = stateDir`, `coding/repo_doc_contract.py:231-238`), and
codex silently tail-truncates the chain at 32 KiB with no log or symptom.

## 3. Revealed preference — what humans chose to write down (125/410 units)

- **65% "requires having been there"**; only 7% trivially repo-recoverable.
- **46% is the high-value quadrant: non-recoverable AND the agent would not know
  to ask** — 91% of that quadrant is durable.
- The content is **operational memory of a toolchain**, not of a codebase:
  GitHub Actions, git, Playwright, doppler, Namespace, Axiom, Modal. Types
  "tool/env constraint + failure trap + gate discipline" = **58%**.
- **Architecture / where-things-live is 11%** — the smallest real category and
  the most repo-recoverable. *The thing memory-for-code products sell is the
  thing these humans chose not to write down.*
- **Humans already built the split we keep theorizing about.** `LEARNINGS.md` +
  `AGENTS.md`: 97% durable, 88% push-not-pull. Session-memory dir: 73% durable,
  58% ask, and it holds **all** the decay. They separated always-inject from
  retrieve-on-demand by putting them in different files.
- They also hand-built supersession and a link graph: **62/410 units carry
  SUPERSEDED/RETRACTED markers, 315/410 carry `[[wikilinks]]`.**

## 4. The call

**Kill the retrieval framing for coding agents.** Three measurements agree, and
the fourth (adherence) explains why more retrieval cannot help: the fact is
already there.

**What is actually unserved, in priority order:**

1. **Adherence / constraint enforcement at the moment of action.** Not "can the
   agent find the rule" but "did the agent obey it". This is the 45%+30% of
   incidents where the fact was present and ignored, and it is what the user
   asked for in their own words three times.
2. **Per-repo runtime & environment profiles.** The (c) slice, nowhere written.
   Verbatim from a session on 07-26: *"once a repo has been run once, we should
   store and know what box to run for the future right? Like Syndai will need
   modal from the get go instead of re-learning at beginning of every code
   run?"* That is a product request, and it is unbuilt.
3. **Cross-repo/cross-project scope.** The one place grep structurally cannot
   reach — proven by the playwright fact being re-derived 5× in a repo whose
   sibling already had the answer.
4. **An always-on trap block**, small and durable (46% of the corpus, near-zero
   decay), pushed unprompted — because by construction the agent does not know
   these traps exist and cannot query for them.

**What to stop selling:** codebase-structure recall ("where does X live"). It is
11% of what humans record, the most grep-recoverable slice, and we lose that
matchup by 38 points.

## 5. The instrument we could not find all day may be in the transcripts

Four corpora were rejected today because none carried labeled supersession arcs.
**Adherence violations do carry a label from outside the statement set: the user
says "no".** Those events are dated, attributable, and occur in ~32% of sessions;
the violated rule is usually citable to a file and line (`AGENTS.md:44`). That is
a candidate instrument with the property every rejected corpus lacked.

**It is not preregistered and no claim is made for it here.** The obvious
death-from-below check comes first and is free: *does a trivial rule that simply
re-injects the top-N rules before every action reduce violations?* If yes, there
is no product beyond a prompt change. That check is the next $0 move, and it must
be written as a Part A before any cell is seen.

## 6. Cheapest real win available right now, and it is not in this repo

**Syndai builds seven memory layers for its coding lane and throws them away**
(`coding_claude_runner.py:136-142`). Before MemPhant sells memory to anyone, the
sister product has memory built, budgeted, evicted-first, and discarded on its
most expensive lane. Wiring that up — plus giving `failure_hints` real coding
signal and `file_memory` any writer at all — is a Syndai change, measurable
against existing coding runs, and it costs no research program.
