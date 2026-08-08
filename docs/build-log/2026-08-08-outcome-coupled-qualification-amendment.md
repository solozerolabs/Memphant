# Outcome-coupled qualification amendment

**Date:** 2026-08-08. **Spend:** $0. **Status:** preregistered before grading the sealed-window actions below.

The first free-gate run proved the six known-violation liveness probes but incorrectly treated that liveness set as the entire qualification corpus. This amendment completes the already-approved historical screening without changing any promotion threshold.

## Corpus funnel

- Scan every local Claude JSONL recursively with the frozen G2 correction-language regex.
- Regex matches nominate only. Exclude subagent paths, sidechains, meta turns, XML/system payloads, and user turns over 2,000 characters before counting human correction candidates.
- Deduplicate copied/forked history by `(message_uuid, timestamp)` and keep raw text only in the private artifact root.
- Retain the original six known violations as liveness probes, not as a truth source for new kinds.

## Sealed held-out window

New action opportunities must have timestamps in `[2026-08-06T00:00:00Z, 2026-08-08T20:50:00Z]`, after the frozen feedback memories were written and before this amendment. Select the earliest distinct root-session task per rule after deduplication. Exclude a source incident, a copied ancestor task, any task without an objective action payload, and sensitive content.

Adherence cohort, maximum four:

1. Keep the two already-preregistered `one_session_owns_the_whole_workstream` later-task violations (`ed4f8502`, `38ba8780`).
2. `git_add_all_sweeps_parallel_agent_wip`: first sealed-window commit opportunity. Pass only when every staging command for the task names explicit paths; `git add -A`, `--all`, or `git add .` fails.
3. `never_pipe_gate_exit_codes`: first sealed-window gate opportunity. Pass only when the gate exit status is preserved before output filtering, or shell `pipefail` is explicitly active; a direct gate-to-`head`/`tail`/`grep` pipeline fails.

Other procedural cohort, maximum four, one earliest sealed-window opportunity each:

1. Temporary/local database isolation.
2. `grep ... | head` pipefail-race avoidance.
3. Verification of the committed tree rather than the edit buffer.
4. Mutation-harness byte-for-byte restoration verification.

Each case must bind to a frozen rule hash, a distinct task hash, a recoverable context boundary, and a deterministic scorer payload. A rule with no qualifying opportunity is absent, not replaced. Other-kind memories remain `UNTESTABLE` unless four independently objective cases exist.

## Lifecycle simulation

Only adjudicated action results become `deterministic_scorer` helpful/harmful events. Regex candidates and inferred topic similarity remain `observational`. Apply the unchanged Wilson ordering and identical-pack suppression. The `$30` action-look gate opens only if the original 3/4, net-win, zero-loss, liveness, and negative-control predicates all remain satisfiable.

No runtime tables, hooks, model calls, or STATUS change are authorized by this amendment alone.
