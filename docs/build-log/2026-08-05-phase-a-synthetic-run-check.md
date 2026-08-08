# Phase A — synthetic run-1→run-2 block check: 3 bugs, block does NOT render yet

**Date:** 2026-08-05 · **Method:** drove the REAL slice functions
(`deterministic_run_facts` → `capture_repo_profile_facts` → worker →
`load_repo_profile_block`) against the local dev Memphant stack with a synthetic
yurivan run. No Temporal, no E2B, no PR, no prod, no credits (per the
run-path decision). The point was to see the block; instead it surfaced three
real defects in dependency order — which is exactly what the check is for.

## Bug 1 (fixed) — retain 422, silently swallowed

`build_semantic_unit_retain_payload` omitted `payload.unit.confidence`, which
the server's `/v1/episodes` schema requires. Every capture 422'd, and fail-open
swallowed it — so in prod this would have looked like "profiles just never
populate," with no error surfaced. Deterministic finalize facts are asserted:
`confidence: 1.0`. Fixed in Syndai adapter. **Lesson: fail-open hid a contract
break; the capture path needs a shadow counter (retain_ok / retain_failed) so a
silent 100%-failure is visible without a debugger.**

## Bug 2 (fixed) — retain 503, unseeded dev tenant

Dev-tenant auth binds every request to tenant `7a1e9c2e…` but never creates the
row; unit writes FK to `tenant`, so every retain 503'd (`backend_unavailable`).
`dev_stack.sh` now seeds it. Ops-only; not a slice bug.

## Bug 3 (THE finding, not yet fixed) — trust/kind mismatch: the block can never render

With 1 and 2 fixed, retain returns 200 and **mints units — but the DB shows
them as `kind=belief, state=candidate, trust_level=agent_output`**, not
`semantic`:

```
belief | candidate | agent_output | This repo runs on the `e2b` sandbox backend; …
belief | candidate | agent_output | Resource tier `small` was sufficient …
belief | candidate | agent_output | Validation commands known to run here: …
```

The slice retains `kind=semantic` from the `syndai:coding-engine` actor, which
is `agent` kind → `agent_output` trust. The write compiler **correctly** refuses
to mint an *active semantic* unit from a low-trust actor (this is the one-plan
Gate-B / RW-3 rule, pinned by `fact_extraction_subject_key_pg.rs`): an
agent-authored assertion becomes an uncorroborated **belief candidate**. Then
`fetch_repo_profile_units` filters `kind=semantic` → sees nothing →
`load_repo_profile_block` returns "" → **every run is untreated, forever.** Not a
stack artifact — a modeling error in the slice, and the trust system behaving
exactly as designed.

### The fix is a design decision, deferred to its own change

Two correct options, one recommendation:

- **(A, recommended) Model profile facts as `Preference`.** Per plan-of-record
  §2 and spec 04 §13.2a, `Preference` is "declared, never promoted; superseded
  or revoked, never decayed" — served without corroboration. A per-repo runtime
  profile IS a declared standing fact about the repo. Change: retain
  `kind=preference`, read `kind=preference`, and confirm an `agent_output` actor
  may mint an *active* preference (04 §13.2a actor-gating — **verify, do not
  assume**; a first probe hit an incidental 409 re-bind collision before this
  was confirmed). If preference also gates on actor trust, fall to (B).
- **(B) Elevate the profile-writer actor to system trust.** These are
  *deterministic machine observations* (finalize KNOWS the sandbox was e2b — it
  is not inferring), so system trust is defensible. Heavier: it is a trust-model
  change with security review, and it widens what that actor can assert. Prefer
  (A) unless (A) is blocked.

Either way the read filter must match the write kind — today they agree on
`semantic`, which is the one kind this actor cannot serve.

## Status

- Slice code paths: exercised end-to-end, fail-open verified (nothing crashed
  when Memphant was misconfigured — the executor would never have noticed).
- Block renders: **NO.** Blocked on Bug 3.
- The Phase A cohort is therefore **not yet accruing treated runs** even with the
  flag on — every run is untreated until Bug 3 lands. The prereg's kill clock
  does not start until the first treated run exists.
- Next: the (A)/(B) decision + a PG-twin test asserting a profile fact is minted
  in a SERVED state and round-trips through `load_repo_profile_block` (this
  synthetic check, promoted to a real test, is the regression guard).

## Resolution (appended after the fixes)

**(A) was refuted empirically, not chosen.** Retaining `kind=preference` from
the `agent`-kind actor STILL minted `belief/candidate/agent_output` (DB-checked).
The trust gate is applied *before* kind: `actor_kind_trust` maps `agent →
AgentOutput`, and any AgentOutput assertion becomes a belief candidate whatever
kind it requests. So kind alone can never rescue it — **(B) was necessary.**

**Fix as landed** (Syndai `37e128f78`, `5e13e1e1b`, `9b03301d9`):

1. **Bug 1 (retain 422):** `payload.unit.confidence` is server-required; added
   `confidence: 1.0` (deterministic facts are asserted).
2. **Bug 2 (retain 503):** dev tenant row unseeded (unit FK). Seed added to
   `scripts/dev_stack.sh` (`2d6a150e`) — idempotent, so the cohort stack is
   reproducible.
3. **Bug 3 (write trust):** a dedicated `syndai:repo-profiler` **system** actor
   → `TrustedSystem` → active `semantic` units. Narrow: distinct from the shared
   `syndai:coding-engine` agent actor, so nothing else is elevated.
4. **Bug 4 (read staleness), found only after 1-3 unblocked the block:** raw
   `/scopes/{id}/memory` returns EVERY generation, so a superseded fact rendered
   ALONGSIDE its replacement (both resource tiers). This is worse than empty —
   contradictory facts in the prompt. Fixed by reading the canonical
   `/projection` (active/validated only, deduped on fact_key).

**Block renders: YES.** Reproduced on three fresh repos — run 1 captures, run 2's
turn-1 block carries exactly the 3 current facts, `treated = YES`, active
`semantic` at `trusted_system` (DB-confirmed). A time-separated restatement
(`small`→`large` tier) supersedes in place: the closed generation drops out of
the projection and only the current fact serves.

**One honest caveat (test artifact, not a slice bug):** `observed_at` is stamped
at *second* precision. Two synthetic "runs" firing in the same wall-clock second
collide and do NOT order, so the restatement looked like a no-op until the writes
were separated in time. Real runs are minutes/hours apart, so this cannot occur
in production — but if finalize ever needs same-second supersession, bump
`observed_at` to sub-second.

**Cohort status:** the flag-on path now produces genuinely treated runs. The
prereg kill-clock can start once this runs on real traffic. The gap between this
synthetic proof and a real yurivan PR is now purely the deploy path (slice +
reachable Memphant on the worker's host), not a code question.

**Regression guard still owed:** promote this synthetic to a PG-twin test
(fact minted in a SERVED state → round-trips through `load_repo_profile_block`,
and a restatement serves only the current generation). The tests team flagged
that any new write path needs a PG twin because InMemoryStore can't see the
trust/exclusion behavior — which is exactly the class of both bugs 3 and 4.
