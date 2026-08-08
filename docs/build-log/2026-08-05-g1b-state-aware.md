# G1b — state-aware veto precision: INSTRUMENT INVALID, and that is the finding

**Date:** 2026-08-05 · **Prereg:** `benchmarks/xs_crosssession/g1b_state_aware.py`
(committed before scoring). **Spend: $0.**

## The scored table, reported and then struck

Replayed session state (rebase-active, preflight-recent, prod-auth,
shared-checkout) + refined predicates over 91,287 tool calls:

| rule | fired | precision (as scored) | mode (as scored) |
|---|---:|---:|---|
| R1b full-gate, no preflight | 429 | 1.000 | ~~VETO~~ |
| R2b bare destructive git, shared tree | 4 | 1.000 | ~~WARN~~ |
| R4b codex argv | 13 | 1.000 | **VETO-candidate (survives, see below)** |
| R6b prod, unauthorized | 42 | 0.143 | DROP |
| R7b pathspec stash in rebase | 7 | 1.000 | ~~WARN~~ |

**These numbers are struck because the instrument failed its own probe.** Per
this program's standing doctrine (a passing gate is evidence of nothing until
you make it fail on purpose), two probes ran before reporting:

1. **Recall probe against ground truth — FAILED.** The two sessions with
   *known, user-corrected* full-gate violations (`15403b3d` 08-04, `9e49b76b`
   08-05) show **zero R1b fires**. The rule catches 429 other gate runs and
   misses both events it exists to catch. Precision 1.0 on the wrong events.
2. **Label-circularity audit — FAILED.** The G1b label criteria auto-assign TP
   wherever the state test fired ("the state test already excluded the
   legitimate phase") — the label trusts exactly the machinery under test.
   Sampled R1b fires are ordinary pre-push gate runs in dedicated worktrees
   whose violation status is precisely the workflow-phase ambiguity G1
   acknowledged; R7b fired on a read-only `git stash show -p` and on a heredoc
   body. A 1.000 measured against assumed labels is not a measurement.

R6b's 0.143 is the honest row (its labels were non-circular: write-verb
required), and it DROPs.

## What survives

**R4b (codex `$(cat …)` argv) survives as the single veto-candidate**: 13
fires, content-anchored, the written rule bans the exact byte pattern
regardless of context, so its labels are not circular. It still does not ship
in veto mode from a retrospective n=13 — see below.

## The real conclusion: retrospective precision measurement has hit its ceiling

Two attempts, same lesson from opposite directions. G1 (input-only) was honest
and showed context-dependence kills static precision. G1b (state-replay) could
only stay honest where labels were anchored outside the predicate (R6b, R4b);
everywhere else the retrospective frame forced circular labels. **There is no
third retrospective cut to run.** Ground-truth violation labels exist only at
the moment a rule fires in a live session and a human (or the session's own
subsequent trajectory) confirms or contradicts it.

**Design consequence, binding on Phase B: shadow mode is the instrument.** The
warn-only hook ships with per-rule shadow telemetry — every fire logs
{rule, tool_input hash, state snapshot, outcome: user-proceeded /
user-heeded / user-corrected-later}. Veto is promoted per-rule when live
shadow precision clears 0.95 over ≥50 fires with zero user-contradicted
blocks-that-would-have-been. The 0.95 bar never moved; the measurement moved
to the only place it can be honest. R4b enters shadow with the strongest
prior; R1/R2/R7-class rules enter as warnings whose text cites the ledger
entry they came from.

## Gate ledger after G1/G1b

- G1: WARN-ONLY stands.
- G1b: invalid as a veto license; R4b veto-candidate pending live shadow.
- Phase B acquires a hard requirement it did not have this morning: the
  shadow-telemetry loop is not optional instrumentation — it is the only
  veto-precision instrument that can exist.
