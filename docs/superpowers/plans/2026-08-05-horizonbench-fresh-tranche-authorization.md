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

**Recommended authorized ceiling: $260** (primary + ~40% headroom for
prompt-token variance and the 1M-context premium on the full-context arm).
Hard fail-safe: stop on reaching the ceiling regardless of completion.

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

## Authorization signature block (owner fills before any paid call)

- [ ] P1 runner un-seal committed; diff sha256: `__________`
- [ ] P2 fresh selection frozen; fresh id-set sha256: `__________`; excluded-union sha256: `__________`; disjointness asserted
- [ ] P3 free construction gate passed; fresh supersessions (closed valid_to / edges): `____ / ____`
- [ ] P4 power freeze passed; signed n = `____` users / `____` items; MDE @80% = `____`
- [ ] Reader pinned: `claude-opus-4-6`, 1M-context route, uncached, structured JSON, 1,024-token cap
- [ ] Authorized ceiling (USD): `______`  (recommended $260)
- [ ] Pilot kill gate armed (10u/20i)
- [ ] Authorized by: `______________`  date: `__________`
- [ ] `authorization_scope_sha256`: `__________` (committed)

Paid execution runs **only** through
`doppler run --project syndai --config dev -- …`, stopping on any §9 condition.
