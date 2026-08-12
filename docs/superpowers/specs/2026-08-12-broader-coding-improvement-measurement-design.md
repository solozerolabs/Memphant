# Broader Outcome-Coupled Coding Improvement Measurement

**Date:** 2026-08-12  
**Status:** approved design, pending written-spec review  
**Spend ceiling:** USD 50, including pilot and confirmation

## Goal and claim boundary

Test whether serving an earlier outcome-validated project lesson improves both:

1. validator-backed completion of later coding tasks; and
2. `accepted_without_violation`, defined as validator pass, requested end-state
   pass, and zero preregistered rule violations.

This is a paired causal measurement of one outcome-coupled delivery mechanism.
It is not a test of automatic lesson extraction, general intelligence, every
coding agent, or all software work. A positive result may support only:

> Under the frozen model, runtime, and heterogeneous task distribution,
> outcome-coupled project lessons improved both task completion and
> violation-free completion without a measured safety, privacy, or cost
> regression.

Model-, mode-, family-, and language-specific results remain visible. No
production behavior changes automatically from this experiment.

## Evidence used to shape the task distribution

A privacy-preserving local census inspected recent file-changing Claude Code,
Codex, and Syndai CaaS transcripts from Syndai, MemPhant, and Yurivan. It did
not export prompts, commands, model text, or transcript bodies.

The descriptive sample contained:

- 150 Claude Code interactive sessions. The 113 substantive validated runs
  had a median 23 directly observed edited files and 42 validation commands.
  Fifty-five used documentation or web tools and 110 used subagents.
- 150 recent Codex coding contexts. The 74 substantive validated contexts had
  a median 2 directly observed edited files and 5 validation commands. Many
  were automatic or delegated fragments rather than root conversations.
- 34 file-changing Syndai CaaS jobs. The 23 substantive validated jobs had a
  median 55-minute transcript span, 6 directly observed edited files, and 4
  validation commands. Fourteen short jobs edited state without reaching a
  validation command and remain representative failure cases.

These are shape estimates, not performance estimates. Durations are transcript
spans rather than active compute time, and file counts cover directly observed
edit/patch operations only. Raw historical tasks are not reused as evaluation
prompts.

## Experimental unit and project clusters

One paired task is one frozen repository commit executed twice in independent
scratch copies:

- `C0`: no learned project lesson;
- `M1`: the one lesson selected by the frozen outcome-coupled projection.

Within a pair, base commit, external prompts, automatic lifecycle, model,
reasoning settings, tools, permissions, time/turn ceilings, validators, and
requested end-state predicates are identical. Only the recorded lesson
exposure may differ. Arm order is randomized before any result is read.

The confirmation set contains 48 paired tasks grouped into 16 independent
project clusters, three later tasks per project. Each project contributes one
task in each execution mode below. Project is the inference cluster; tasks
inside one project are never treated as independent evidence.

Projects are isolated snapshots derived from real coding work or reproducible
public repository history. Every task must have a clean base commit, a
pre-arm requirement packet, a hidden deterministic validator, and a known
feasible end state. No gold patch, hidden test body, or later outcome enters an
agent prompt.

## Execution modes

### 1. Syndai CaaS one-shot: 16 pairs

One external job submission asks the agent to complete the work end to end.
The existing CaaS automatic lifecycle may emit internal feedback and tool
turns; those do not turn the job into an interactive task. Lifecycle settings
remain byte-identical across arms.

Target shape: 45--90 minutes of allowed agent time, 3--10 expected changed
files, and multiple focused validation opportunities. Aborted, timed-out, or
unvalidated jobs remain scored failures.

### 2. Interactive multi-turn: 16 pairs

Each task has two to four frozen external turns representing a real workstream:
initial implementation, a deterministic milestone or clarification, and final
verification/handoff. The turn sequence and bytes are fixed before dispatch;
they do not adapt to which arm is doing better.

Target shape: multi-file features or investigations, normally 10--30 expected
changed files, with resumptions and intermediate failures allowed. Context
compaction and default subagent behavior remain enabled when the frozen runtime
would normally use them.

### 3. Short or delegated: 16 pairs

Focused 5--20 minute tasks model Codex-style decomposition and small follow-up
work. They normally touch 1--3 files and still require an objective validator.
The root receives the projected lesson; descendants receive only what the
unchanged agent runtime normally inherits. The exposure ledger records what
was actually shown rather than inferring propagation.

## Task families and technology coverage

The 48 confirmation tasks are balanced across six eight-task families:

1. root-cause bug or regression repair;
2. multi-file backend or API feature;
3. UI feature with browser-observable behavior;
4. framework or language integration requiring official documentation;
5. persistence, migration, restart, concurrency, or idempotency work;
6. repository process, recovery, generated-source, or scope discipline.

The frozen census must cover Python, TypeScript, Rust, and Go. No language may
hold more than 40% of tasks. UI tasks use browser-backed interaction,
accessibility, and specified visual-state checks; subjective design taste is
not scored. Documentation tasks use read-only official documentation snapshots
bound to URL, version, retrieval date, and SHA-256. Both arms receive the same
documentation access.

## Lesson provenance and leakage boundary

Every active lesson must:

- be authored and hash-locked before its held-out tasks;
- be validated by an earlier, distinct task outcome in the same project;
- have complete outcome-to-exposure linkage;
- state a reusable project rule rather than an answer to a held-out task;
- predate every held-out task in which it can be selected; and
- have no held-out symbol, literal, file path, test name, or gold-patch fragment.

Candidate lesson text may be prepared manually, but only the existing
outcome-backed lifecycle may activate it. This deliberately tests causal
selection and delivery, not automatic transcript-to-lesson extraction.

At least eight confirmation tasks are preregistered relevance-negative
controls. Their treatment projection must be empty. Unexpected admission,
missing admission, multiple lessons, or lesson/trigger drift invalidates the
affected pair before scoring.

An empty treatment projection is byte-identical to control, so the existing
identical-context suppression rule dispatches that task once and reuses its
checksum-bound result. These negative controls test admission precision and
cost suppression; they do not enter the efficacy estimate. The co-primary
estimate therefore uses the frozen relevance-positive subset, with at least
two such tasks in every project cluster.

## Model and provider lock

The primary model is OpenRouter
`anthropic/claude-sonnet-5`, canonical slug
`anthropic/claude-sonnet-5-20260630`, using the existing Claude coding runner.
The catalog snapshot, endpoint metadata, pricing, model response identity, and
runner version are hash-locked before dispatch. Provider fallback is disabled.
Any served-model mismatch, unsupported parameter drop, ambiguous completion,
or automatic redispatch invalidates the cell and receives no retry.

The alternative OpenRouter model discovered during design is
`deepseek/deepseek-v4-flash-0731`, canonical slug
`deepseek/deepseek-v4-flash-20260731`. It is not used in this campaign because
OpenRouter guarantees Claude Code compatibility only for Anthropic models;
adding a new coding-agent adapter would confound the memory treatment.

Only the secret-consuming dispatch command runs under the authorized Doppler
`syndai/dev` wrapper. Secrets, provider values, prompts, and model bodies are
never written to the repository or printed into public evidence.

## Pilot and spend control

Run a 12-pair construction pilot first, with four pairs from each execution
mode. Pilot tasks are disjoint from confirmation and never enter the
confirmatory effect estimate.

Pilot reserve: USD 8 total. Confirmation reserve: USD 42 total. Cumulative
settled and outstanding reservations may never exceed USD 50. Every provider
attempt is durably journaled before dispatch and settled afterward. An
ambiguous provider completion is terminal and is not retried.

The pilot opens confirmation only if:

- every base, prompt, lesson, model, lifecycle, validator, and scorer hash is
  complete and unchanged;
- all relevance-positive treatments have exactly one linked exposure and all
  relevance-negative treatments have none;
- all validators reject their unmodified base and accept the owner reference
  end state;
- no raw-content or secret scanner fails;
- no treatment-only correctness, safety, or privacy regression occurs;
- all three execution modes produce at least one valid pair; and
- the predeclared primary instrument has a computed, non-null 80%-power MDE
  for the fixed 16-project confirmation design.

Pilot effect sizes are diagnostic and cannot be called improvement. Failing a
gate stops before confirmation.

## Outcomes and deterministic scoring

For every task and arm:

- `task_success = deterministic_validator_pass AND requested_end_state_pass`;
- `accepted_without_violation = task_success AND zero_rule_violation`.

Each task preregisters its validator command, requested-state predicate, and
finite violation predicates. Validators run outside the agent after the job
ends. An LLM judge cannot decide either primary outcome.

Secondary descriptive metrics are task duration, input/output tokens, settled
cost, tool calls, changed-file count, validation attempts, intermediate
failure/recovery, compactions, and subagent count. Cost is not a post-result
tuning knob.

## Confirmatory inference

The two co-primary outcomes are task success and
`accepted_without_violation`. The broader joint claim passes only when both
pass independently.

Primary inference is an exact two-sided project-cluster sign test over each
project's mean paired treatment difference. This prevents the three tasks in
one project from being counted as independent. Task-level exact McNemar cells
are published descriptively, along with mode, family, language, and project
cluster tables.

For the cluster test, each project is positive, negative, or tied according to
the mean paired difference over only its preregistered relevance-positive
tasks. Ties are retained in the census and excluded from the conditional sign
count. Power integrates over the pilot-banked probability that a project is
non-tied. Its MDE is reported in the cluster-sign unit: the minimum absolute
difference between the probabilities that a non-tied project favors treatment
and favors control. The public artifact also reports the task-level marginal
percentage-point difference, but does not mislabel that descriptive value as
the cluster instrument's MDE.

For each co-primary outcome, all of the following are required:

- exact cluster test `p <= 0.05`;
- a computed, non-null 80%-power MDE from the frozen design and pilot-banked
  nuisance rate;
- zero task-level treatment regressions;
- at least one treatment win in every execution mode;
- wins in at least four of six task families; and
- no negative marginal delta in any mode, family, or language stratum.

Because the claim is conjunctive, failure of either co-primary outcome makes
the broader result flat, harmful, invalid, or underpowered as appropriate.
Success on violation avoidance cannot compensate for flat correctness.

## Linkage, restart, and privacy gates

Reuse the existing task-outcome and task-memory-event endpoints plus the local
JSONL spool. Every cell requires a validator-backed terminal outcome and an
explicit exposure record. Helpful or harmful attribution without recorded
exposure is rejected.

The campaign includes one after-accept client restart in each execution mode,
idempotent replay, complete drain, and zero residual spool rows. Missing task,
arm, validator, endpoint receipt, project binding, exposure edge, retry
settlement, or privacy field invalidates the campaign rather than shrinking
the denominator.

Raw prompts, documentation bodies, command streams, patches, model messages,
and transcripts remain in a checksum-bound private directory. Public evidence
contains only anonymous IDs, hashes, counts, aggregate scores, cost, power,
and gate predicates. A recursive forbidden-field and high-entropy scanner must
pass before the public artifact is written.

## Deliverables

1. A committed preregistration artifact with task/project census, hashes,
   randomization, model lock, spend ledger authorization, cluster instrument,
   and evidence contract.
2. Private task packets, provider streams, patches, and checksum manifest
   outside the repository.
3. A public-safe pilot artifact, even when the pilot stops.
4. A public-safe confirmation artifact only when every expected pair has a
   terminal receipt.
5. An addendum to `docs/flows/outcome-coupled-evolution.md` recording the
   frozen gate and the eventual narrowly worded result.

## Explicit non-goals

- no production rollout or prompt-policy change;
- no automatic lesson extraction or capture claim;
- no model-general or agent-general claim;
- no subjective UI-quality claim;
- no adaptive task replacement after results are visible;
- no provider fallback or retry of ambiguous paid calls;
- no raw transcript or prompt publication; and
- no claim when linkage, privacy, power, or corpus completeness is missing.
