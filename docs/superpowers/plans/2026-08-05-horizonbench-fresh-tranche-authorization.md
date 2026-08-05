# HorizonBench fresh-tranche authorization packet (DRAFT — unsigned)

Date drafted: 2026-08-05
Status: **DRAFT for owner authorization. No paid call is authorized by this
document until the signature block at the end is filled and its
`authorization_scope_sha256` is committed.**
Spend authorized so far by this packet: **$0.**

Prior packets this supersedes for *nothing* — it adds a new arm on new users and
leaves every earlier failed-closed authorization intact:
`docs/build-log/artifacts/horizonbench-confirmation-v7/authorization.json`,
`docs/build-log/artifacts/horizonbench-confirmation/authorization.json`.

## 1. Why this run exists

The 2026-08-03 powered confirmation measured Fast at −15.8pp overall vs full
context (95% CI [−24.2, −7.5]) on a 60-user tranche. We now know that run had
`MEMPHANT_FACT_EXTRACTION=0`, so supersession was structurally unreachable
(1,448/1,448 evidence units auto-keyed, 0 closed generations), and that even
with extraction on the subject key was lexical, so a preference restated in
different words never superseded
(`docs/build-log/2026-08-05-horizon-stage1-supersession-defect.md`).

Both are now fixed: extraction is default-on in the runtime, and semantic
subject identity ships behind `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD`
(calibrated 0.85), commits `4e4a19eb`, `a2c5bb86`. Chat-lane non-inferiority
holds on the frozen LME-S dev split (recall@5 unchanged, recall@10 0.8012→0.8072,
zero per-question losses).

This run tests, on **fresh users the fix has never touched**, whether the
repaired current-state compiler moves Fast from clearly-losing toward
non-inferior to full context on evolving preference state.

## 2. Claim boundary (what this can and cannot establish)

- **Can:** a decisional paired result for a fresh held-out HorizonBench tranche,
  under the exact repaired configuration, with user-clustered inference.
- **Cannot:** an official-complete HorizonBench score, an overall-SOTA claim, or
  a cross-axis near-SOTA claim. Those remain false and are out of scope
  (`docs/superpowers/plans/2026-08-03-multi-axis-near-sota-program.md` §Claim
  Contract). The 4,245-item treatment is **not** authorized here.
- The burned 60-user tranche and the 10 pilot users are **never** reused,
  re-scored, or relabeled as held out.
- Synthetic-persona and LLM-judge limitations apply and must be reported; a high
  score is not evidence about real-user distribution shift.

## 3. Preconditions — all $0, all must pass before the signature block is valid

**P1. Un-seal the confirmation path for the fix (code change, no spend).**
`scripts/run_horizonbench.py`'s `build_confirmation_evidence` and
`run_paid_confirmation` currently hardcode `MEMPHANT_FACT_EXTRACTION="0"` and no
resolution (lines ~2717, ~2850). This packet requires the same treatment already
applied to the free sample path (commit `3e810034`): honor
`MEMPHANT_FACT_EXTRACTION` and `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD` from the
environment via `setdefault`, and record the *actual* flags that ran in the
evidence contract (reuse `fact_extraction_flag()`; add the resolution threshold).
The change must be committed and its diff hash pinned in the signature block. The
sealed default stays off; this only makes the fix expressible.

**P2. Fresh selection, hash-bound and provably disjoint (`$0`).** Add a
`select-fresh-tranche` step (or extend `select-confirmation`) that:
- loads the pinned release (`stellalisy/HorizonBench@50941f00…`, corpus sha256
  `69a7f5cd…`, verified by the existing census);
- builds `excluded_user_ids` = union of
  - the 60 confirmation users (`selection.json:expected_user_ids`),
  - the 10 exposed pilot users (hash `100de4ce…`),
  - the 2 drift/collision users (`gemini-3-flash/user_15`, `gemini-3-flash/user_49`);
- selects with a **new seed** `horizonbench-fresh-v1` (never the confirmation
  seed `horizonbench-confirmation-v1`) via the existing seeded-hash ranking,
  one evolved + one static item per user, using identity/generator/stratum only —
  never `correct_letter` or `distractor_letter`;
- **asserts the fresh id-set is disjoint** from every excluded id and writes
  `fresh_user_ids_sorted_sha256` + `excluded_union_sha256`. Fail closed on any
  intersection.

Fresh eligible pool (census `eligible_user_counts` 118/103/90 = 311, minus the
72-user burn set) is ≈ 240 users, so the tranche size in §5 is reachable with
margin. The exact fresh count is emitted by this step, not asserted here.

**P3. Free construction gate (`$0`).** Retain each fresh user's monotone timeline
prefixes incrementally and produce gold-blind Fast evidence with the fix on
(`MEMPHANT_FACT_EXTRACTION=1`, `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD=0.85`,
`MEMPHANT_DEEP=off`). Gate requires: every row non-degraded, retained == compiled,
and — the fix-specific check — **non-zero supersession on fresh users**
(≥1 closed `valid_to` and ≥1 `supersedes` edge in the fresh scope, measured in
the database, never in served evidence). If the fix produces zero supersessions
on fresh data, stop here: there is nothing new to pay to measure.

**P4. Power freeze (`$0`).** Run `scripts/instrument_power.py` for the fresh
tranche's design and pin the MDE at 80% power for the preregistered predicate in
§7, under the burned tranche's observed discordance rate (b+c = 31/120 ≈ 0.26) as
the planning prior. The signed n must clear that MDE. Record
`benchmarks/manifests/instrument_power.json` delta.

**Design decision, made once at P4 and frozen — single-stage or group-sequential.**
The selection is prefix-stable by construction: `select_confirmation_rows` ranks
all eligible users by `_seeded_key(seed, generator, user_id)` and takes the first
N per generator, and each user's two items are chosen independently of N. So
under one seed the 60-user set is an **exact prefix** of the 100-user set — same
users, same items. This makes a group-sequential design cache-efficient, but it
is legitimate *only if elected here, before any paid look.*

- **Single-stage:** freeze one n (recommended 100), run once. Simplest;
  no interim peek.
- **Group-sequential (down-payment):** pre-register n₁ = 60 (interim) and
  n_max = 100 (final) under the **same seed** `horizonbench-fresh-v1`, a frozen
  reader, and a frozen Fast config (threshold 0.85). Pin an **alpha-spending
  boundary** (e.g. O'Brien–Fleming, recorded in `instrument_power.json`) so the
  interim look does not inflate type-I error, and a written continue/stop rule
  keyed only to the boundary — never to an unblinded eyeball of the interim
  delta. The stage-2 extension re-presents the same 60 users through the shared
  reader cache dir (`cache_role: read`), so their 240 calls are cache hits and
  stage 2 pays only the 40-user / 160-call increment. Cache reuse requires the
  reader model, prompt version, and Fast config to be **byte-identical** across
  stages; changing the threshold or embedder after the interim look invalidates
  the Fast-arm cache (the full-context arm still hits) and forfeits the saving.

**The burn rule is why this must be elected up front.** A standalone 60-user run
that is scored and *then* used to decide whether to continue has burned those 60
users; reusing them in a later "held-out 100" would relabel burned data as held
out, which the claim contract forbids. In that case the powered run needs fresh
users disjoint from the burned 60 and pays a full second time (see §8). Only the
pre-registered group-sequential path preserves both the users and the cache.

## 4. Frozen inputs (pinned by P1–P4 before signing)

| input | value |
|---|---|
| dataset | `stellalisy/HorizonBench@50941f00f90c03a5a60219d76393869b757b835a:benchmark/test` |
| corpus sha256 | `69a7f5cd9e9f0fca330dacd217339bf3c4f0555b7d14903af6b5032108a886da` |
| fresh selection seed | `horizonbench-fresh-v1` |
| excluded-union sha256 | *[pinned by P2]* |
| fresh id-set sha256 | *[pinned by P2]* |
| fast evidence sha256 | *[pinned by P3]* |
| runner diff sha256 | *[pinned by P1]* |
| reader model | first-party Anthropic `claude-opus-4-6` (the retained pre-release Nov-2025 Opus snapshot; identical to v7 for comparability) |

## 5. Arms and exact configuration

Two arms, paired per item, one reader model, independent uncached prompts,
native structured JSON, 1,024-token output cap — identical to v7 except the Fast
arm's substrate carries the fix.

| arm | evidence | env |
|---|---|---|
| `full_context` | complete monotone timeline prefix | reader only; **1M-context route required** (v7's full-context arm hit an OpenRouter 200k limit at 268k tokens before moving to the 1M route — preflight that every full-context prompt fits) |
| `fast` | non-degraded Fast recall with the fix | `MEMPHANT_FACT_EXTRACTION=1`, `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD=0.85`, `MEMPHANT_DEEP=off` |

**Recommended tranche size: 100 users / 200 items** (33/33/34 per generator),
400 reader calls. Rationale: v7 at 60 users/120 items produced a 17pp-wide CI —
adequate to *detect* the large −15.8pp effect, too wide to support a
*non-inferiority* claim (the ±5pp margin in the program claim contract). 100
users tightens the CI enough for §7's predicate to be decidable. The exact n is
frozen by P4; if P4 shows 100 is under-powered for the ±5pp margin, raise it (the
fresh pool allows up to ≈120/gen) or narrow the claim to "materially improved,
not yet non-inferior."

Conservative alternative: reuse the exact v7 design (60 users/120 items, $140
ceiling) for a like-for-like before/after; it can show improvement but likely
cannot support a non-inferiority claim.

## 6. Preregistered outcome predicates (frozen before scoring)

Decisional. All evaluated on complete paired rows with user-clustered bootstrap:

1. **Primary (non-inferiority):** the one-sided 95% LCB of (Fast − full context)
   overall delta is ≥ −5pp.
2. **Directional improvement vs the burned baseline:** overall delta strictly
   greater than the v7 point estimate (−15.8pp) — i.e. the fix demonstrably moved
   the needle. (Descriptive, cross-tranche; not a paired test.)
3. **Evolved lift:** evolved-item delta > 0.
4. **Distractor non-regression:** evolved distractor selections do not increase
   vs the matched full-context arm.
5. **Discordance floor:** ≥ 6 discordant pairs (else underpowered — inconclusive,
   not a pass).

Report: per-arm accuracy overall/evolved/static, paired delta + 95% CI,
discordant split, and the full UX rail (ingest cost, stored bytes, recall + e2e
p50/p95, prompt tokens, paid cost, retries, unsettled liability).

A pass on (1)+(3)+(4) is the axis-move result. A fail on (1) with a pass on (2)
is honest partial progress and is published as such — not as a near-SOTA claim.

## 7. Pilot kill gate (spend-limiting)

Before the full run, a **10-user / 20-item paid pilot** on the first fresh users
(by the frozen ranking), same arms and reader. Kill conditions — stop and do not
release the remaining spend if any holds:
- Fast overall accuracy on the pilot is worse than the v7 tranche's Fast
  (36.7%) by more than its noise band, i.e. the fix made things worse;
- any provider/model/price drift vs the pinned reader;
- incomplete pricing or unsettled liability;
- the free P3 supersession check did not reproduce on the pilot users.

The pilot spend (~$18 at the $/call below) counts against the ceiling.

## 8. Cost preflight and ceiling

Basis: v7 settled $109.1346 for 240 calls = **$0.4547/call** (first-party Opus
4.6, uncached, 1,024-token cap). Recomputed at freeze from the actual fresh
prompt-token distribution — this planning figure is not the authorization.

| design | items | calls | est. cost |
|---|---:|---:|---:|
| pilot (10u/20i) | 20 | 40 | ~$18 |
| **primary (100u/200i, incl. pilot)** | 200 | 400 | **~$182** |
| v7-equivalent (60u/120i) | 120 | 240 | ~$109 |

**Recommended authorized ceiling (single-stage 100u): $260** (primary + ~40%
headroom for prompt-token variance and the 1M-context premium on the
full-context arm). Hard fail-safe: stop on reaching the ceiling regardless of
completion.

**Group-sequential reuse (only under the P4 down-payment election).** Stage 1
scores 60 users (240 calls, ~$109) and writes the shared reader cache. Because
the 60-user set is a seed-prefix of the 100-user set (§P4) and the reader +
Fast config are frozen identically, stage 2's first 60 users are cache hits;
stage 2 pays only the 40-user increment:

| stage | new calls | cached calls | est. incremental |
|---|---:|---:|---:|
| stage 1 (interim, 60u) | 240 | 0 | ~$109 |
| stage 2 (extend to 100u) | 160 | 240 | **~$73** |
| **combined powered 100u** | 400 | — | **~$182 total** |

So the sequential path reaches the same powered 100-user result for the same
~$182 of actual spend as running 100 in one shot — the stage-1 money is a down
payment, not an added cost. **This holds ONLY if the design is elected at P4
before stage 1.** If stage 1 is instead run standalone and the decision to
extend is made afterward, the 60 are burned: the powered run then needs 100
fresh users disjoint from them and pays a full second ~$182 (≈ $290 total, and
~160 of the ~240 fresh users consumed). Cache reuse also evaporates if the
reader model, prompt version, or Fast threshold changes between stages.

Two ceilings to authorize if electing group-sequential: **stage 1 $140**
(interim), **combined $260** (releases stage 2's ~$120 increment only after the
P4 alpha-spending boundary says continue). Either stage stops hard on its own
ceiling.

## 9. Fail-closed conditions (any ⇒ stop, preserve partial evidence)

Model/provider/price drift; incomplete or missing pricing; unsettled liability;
spend cap reached; any full-context prompt exceeding the reader's context window
after preflight; a fresh-id/excluded-id intersection; a degraded Fast evidence
row reaching the paid stage; a reader/judge fingerprint mismatch vs the frozen
inputs.

## 10. Evidence artifacts (committed; bodies stay out of git)

Under `docs/build-log/artifacts/horizonbench-fresh-v1/`: `census-reuse.json`,
`fresh-selection.json`, `fast-gate.json` (with the fresh supersession counts),
`authorization.json` (signed), `paid-census.json`, `reader-attempts.jsonl`,
`paid-rows.jsonl`, `reader-closure.json`, `result.json`. `result.json` binds
source, selection, construction evidence, authorization, reader attempts,
repository tree, runner diff, and tests by SHA-256, exactly as v7 does. Dataset
bodies and paid caches remain under the protected cache root; commit only locks,
census, authorization, closure, and evidence-contract artifacts.

## 11. Post-run

Settle accounting; register the evidence contract; update only the Horizon row in
`STATUS.md`; stop if the primary predicate fails. If it passes, this becomes the
evolving-preference axis's development evidence — the cross-axis claim still
requires every other axis independently (program plan §Required Portfolio).

---

## Preconditions status (P1–P4 complete, $0, committed `61fd0c57` + P3)

- [x] **P1** runner un-seal committed; `scripts/run_horizonbench.py` sha256 `19aa98395ec760d30a0b9af6369adabcf728a8b27a66663ac407c028467bfc6f`. Confirmation path now honors `MEMPHANT_FACT_EXTRACTION` + `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD` and records the actual flags; sealed default byte-identical to v7.
- [x] **P2** fresh selection frozen, seed `horizonbench-fresh-v1`, disjointness asserted (∩ burn set = 0, re-derived independently). excluded-union sha256 `6005b8778e1850a7146c36f730cf13acfe212dff6553dc0eb15152235067af87`. Interim 60u (30/gen×2) fresh id-set sha256 `e2ef8c41b9da1399c999922da9838f273d5792d83c0e215ba171745f1a7e3eff`; n_max 102u fresh id-set sha256 `a8533be85b10ed1ce20e53560daecac08e7a442a35f7cf9024139abbcfcaa936`; interim nests exactly in n_max at user and item level. Burn set = 60 confirmation + 10 pilot + 2 drift + 6 date-integrity (union 77 after 1 overlap); date-integrity artifact sha256 `852bfabc581bffc90b6542155c764a5cdc05a8955ea4b4171547657764405f85`.
- [x] **P3** free construction gate **PASSED** on the fresh 60-user interim, fix on: 120/120 non-degraded, 9513 retained == compiled, and on fresh users **398 supersessions / 796 supersede edges / 398 closed generations** (gate required ≥1 each). Evidence: `docs/build-log/artifacts/horizonbench-fresh-v1/p3-supersession-evidence.json`.
- [x] **P4** power freeze recorded (`benchmarks/manifests/instrument_power.json`, `preference/horizon` lane, psi 0.2583 from v7): MDE 13.4pt at the interim n=120 vs the 10.8pt decision gap ⇒ interim is a **look, not a decision**; required_n = 183 items, so the n_max **102u / 204i** design is adequate. `instrument_power.py --check` passes.

## Authorization signature block — SIGNED 2026-08-05

- [x] Design elected: **group-sequential** — n₁ = 60u/120i (interim **look**, not a decision) → n_max = 102u/204i. Alpha-spending: **O'Brien–Fleming, 2 looks**, information fractions {0.5882, 1.0}, two-sided α = 0.05. Interim nominal two-sided α₁ = **0.0106** (critical z₁ = 2.556); the interim can stop early only for *overwhelming* efficacy, otherwise the study proceeds to the final look with the remaining α (final nominal ≈ 0.048 by Lan-DeMets). Early-stop-for-harm is handled by the pilot kill gate below, not by α-spending.
- [x] Reader pinned: `claude-opus-4-6`, first-party Anthropic, 1M-context route, uncached, structured JSON, 1,024-token cap. **Verified resolvable 2026-08-05** (model-list check, no spend) — model-drift fail-closed condition clears.
- [x] Authorized ceilings (USD): group-sequential **stage 1 $140** + **combined $260**. Stage 2's ~$120 increment releases only after the interim OBF boundary and the pilot kill gate both permit continue. Either stage stops hard on its own ceiling.
- [x] Pilot kill gate **armed** (10u/20i), kill conditions per §7.
- [x] Authorized by: **Sid Sharma (chat approval 2026-08-05)**
- [x] `authorization_scope_sha256`: `f211d491609a2ec16dbdbcc117bbf35c2ebefa16af605987b45a8f9d5d813dd3` (committed: `docs/build-log/artifacts/horizonbench-fresh-v1/authorization-scope.json`)

Paid execution runs **only** through
`doppler run --project syndai --config dev -- …`, stopping on any §9 condition.

**Runtime gate before the first charge (not yet done — the honest edge of $0):**
the fresh-tranche *paid* pilot runner does not yet exist — `run-paid-pilot` is
hardwired to the retired 10-row v7 sample (`--fast-evidence …/horizonbench-pilot/…`),
and "first 10 fresh users" needs a defined global ordering across the three
per-generator ranks. Building and **dry-run-verifying** that scoped runner
(no provider calls) is the last $0 step; only its verified output is fed to the
first `doppler run` charge. Authorization is signed and the budget is live, but
no provider call has been made.
