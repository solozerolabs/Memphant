# HorizonBench evolving-preference axis — stage-1 interim with the fix ON (closed negative)

**Date:** 2026-08-06. **Spend:** $107.058 settled, $0 unsettled. **Decisional:** no
(interim *look*; n_max deliberately declined). **Outcome:** the subject-resolution
fix helped the point estimate but did **not** rescue Fast; the axis stays negative
and nothing promotes.

## What ran

The signed group-sequential packet
(`docs/superpowers/plans/2026-08-05-horizonbench-fresh-tranche-authorization.md`,
`authorization_scope_sha256 f211d491…`) authorized a stage-1 interim on a **fresh
60-user / 120-item tranche** (seed `horizonbench-fresh-v1`, disjoint from the 77
burned users), reader pinned first-party `claude-opus-4-6` on the **1M-context
route**, uncached, structured JSON, 1,024-token cap. Fast arm carried the fix:
`MEMPHANT_FACT_EXTRACTION=1`, `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD=0.85`,
`MEMPHANT_DEEP=off`. The P3 free construction gate had already passed (398
supersessions on fresh users, 0 degraded rows). Interim authorization
`aa20f05b…`; cost preflight passed (projected $120.15 < $140 ceiling).

240/240 paired terminal rows completed. Machine result:
`docs/build-log/artifacts/horizonbench-fresh-v1/interim-result.json`; closure
`reader-closure.json`; census `paid-census.json`.

## Result (60u/120i, fix ON)

| arm | overall | evolved | static | distractor picks | abstentions |
|---|---|---|---|---|---|
| full_context | 42/120 = **35.0%** | 33.3% | 36.7% | 12 | 5 |
| fast | 31/120 = **25.8%** | 23.3% | 28.3% | 18 | 0 |

Paired Fast − full: **−9.17pp** (evolved −10.0, static −8.3), 95% user-cluster
bootstrap CI **[−18.33, 0.0]**, discordant 31 (10 gains / 21 losses / 89 ties).

Preregistered predicates: **non-inferiority FAIL** (LCB −18.33 ≪ −5pp),
**evolved lift FAIL** (−10.0pp), **distractor non-regression FAIL** (Fast 18 vs
full 12); "improvement vs v7 −15.8" passes descriptively (cross-tranche, not a
paired test); discordance floor passes (31 ≥ 6).

**OBF interim rule:** efficacy-stop requires p < 0.0106 in the favorable
direction; the delta is negative, so no early stop — the protocol would proceed
to n_max.

## Reader UX rail

Fast cuts prompt tokens **92.6%** (mean 164k → 12k), cost **92%** ($0.826 →
$0.066 / call), latency p50 12.6s → 9.0s, p95 57.7s → 14.8s. Totals: 240 priced
attempts (241 including the one superseded usage-cap error), 21,171,223 prompt
tokens, $107.058 settled, $0 unsettled, 0 unrecovered errors.

## Interpretation

The fix moved the overall point estimate **−15.8pp (v7) → −9.17pp**, but that
comparison is **cross-tranche and confounded**: this fresh tranche is materially
harder — full_context scored 35.0% here vs 52.5% in v7, and only 5/120 full
abstentions explain little of it (the ~164k–406k-token full-context prompts
likely degrade the reader). So the improvement is real in the point estimate but
not a clean before/after. On this look Fast remains **clearly inferior** on
evolving preference; the subject-resolution fix helps but does not close the gap,
and Fast's large UX win does not buy back the accuracy deficit.

## Decision

Owner (Sid, chat 2026-08-06) **stopped at the interim and declined n_max**
(102u/204i, ~$73 increment): the look is decisive enough for this purpose even
though it is not statistically powered for the ±5pp non-inferiority claim. The
axis stays negative; no default, checkbox, or SOTA claim moves. Do not reopen or
run n_max without a new mechanism.

## Operational notes

- **Account usage cap mid-run.** At call 79 the Anthropic first-party account hit
  a usage limit (HTTP 400, "regain access 2026-09-01"). The runner retried 4× and
  stopped, preserving partial evidence (§9) — $80.00 spent to that point, $0
  unsettled. The limit was raised and the run resumed cleanly. Not a §9 model/
  context failure: the 1M-context first-party route served 400k-token prompts
  without issue.
- **Path collision.** `run-paid-confirmation` hardcodes its output paths
  (`paid-rows.jsonl` / `reader-attempts.jsonl` / `reader-cache/`). The pilot had
  left its bodies on those names; they were archived to `pilot-*` before the
  interim so it ran as a clean, self-contained campaign (no shared cache).
- **Resume required pruning the errored row.** The resume loop skips any (id,arm)
  already present, so the one `status:error` terminal row had to be removed from
  `paid-rows.jsonl` before resuming; the re-attempt's priced result then
  supersedes the journal error via the ledger transient-retry fix, and the
  campaign closes with 240 priced / 241 total attempts.
