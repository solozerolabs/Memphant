# Phase A cohort measurement — preregistration (committed before the flag flips)

**Date:** 2026-08-05 · **Slice:** Syndai per-repo runtime profile
(Syndai branch `Syndai-memphant-profile` @ `3ebd1a147` + liveness marker;
Memphant `265a063b` kind-filter). **Flag:** `MEMPHANT_REPO_PROFILE_ENABLED`,
flipped on **dev only** after this document is committed. Prod stays off.

## Treatment and cohorts

- **Treated run:** `coding_execution_attempts.executor_metadata.repo_profile_sha`
  is present (the block actually rendered into the turn-1 prompt). Flag-on runs
  where MemPhant was down/empty carry no marker and are **excluded from both
  cohorts** (they received no treatment and are not clean controls — their
  failure may correlate with infra state).
- **Control run:** flag-off runs on the same repos, same date window ±14 days.
- Unit of analysis: coding RUN (not attempt). Same-repo pairing preferred;
  report per-repo strata alongside the pool.

## Endpoints (all from EXISTING run metadata; no new instrumentation)

1. **Primary: repair-turn count per run** (existing repair-bounds telemetry).
2. Secondary: `EMPTY_DIFF` repair occurrences; attempt cost (cents); run
   wall-clock; `waiting_on_approval`/HITL interruptions.
3. Mechanism-liveness (gates reporting, not an endpoint): ≥1 profile fact row
   served per treated run (MemPhant DB count, never served-evidence), and the
   finalize capture writing ≥1 fact per completed treated run.

## Decision rule (fixed now)

- **Minimum n: 30 treated runs.** Below that, no verdict is written — dev
  traffic is low; the cohort accrues for as long as it takes.
- **Success:** median repair-turns strictly lower in treated runs (one-sided
  Mann-Whitney, α=0.05) OR ≥20% relative reduction in EMPTY_DIFF repairs with
  cost non-inferior (treated median cost ≤ 110% of control).
- **KILL (plan §9, executed without renegotiation):** at n≥30 treated, neither
  success condition holds AND treated cost or wall-clock is worse by >10%
  median ⇒ the MemPhant-served profile is removed; the profile pillar demotes
  to plain checked-in files (devil's-advocate attack 4 wins). The block
  renderer and capture code stay (they feed the files); the serve path goes.
- **Flat (neither success nor kill):** one iteration on profile CONTENT is
  allowed (facts chosen, not machinery), then the rule re-applies terminally.

## Confound honesty

Dev traffic is one user's traffic (the G2 caveat applies to this cohort too);
repo mix is nonstationary across weeks; flag-period vs marker-period drift is
handled by the marker (treatment is per-run, not per-period). None of these are
fixable at dev scale — which is why the kill bar demands a visible effect, not
a subtle one. A subtle win at n=30 on one user is indistinguishable from noise
and is treated as flat, not success.

## Ops notes

- Flip: `doppler secrets set MEMPHANT_REPO_PROFILE_ENABLED=true --project
  syndai --config dev` (requires `MEMPHANT_API_BASE_URL`/`MEMPHANT_API_KEY`
  already set in dev; verified before flip).
- Analysis query keys on `executor_metadata->>'repo_profile_sha'` — no schema
  change needed.
- $0 additional spend; the profile serve path is deterministic (no LLM).
