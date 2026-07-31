# Instrument Register — is our measuring apparatus valid, correct, and powered?

**Date:** 2026-07-31 · **Branch:** `af-w7-register` · **Spend:** $0 (no paid model call)
**Machine-readable:** `benchmarks/manifests/instrument_register.json` and
`benchmarks/manifests/instrument_power.json` (regenerate with `scripts/instrument_power.py`)

Owner directive in effect: *stop improving until instrumentation is sound; do not waste
money, but do not run underpowered tests either; verify that every benchmark we use is
not incorrect, diluted, or errored.* This audit gates all further spend.

**Method.** Every count below was taken from the shipped rows — `jq`/Python over the
actual JSON/JSONL on disk, or a fresh fetch of the pinned revision. Nothing is taken from
a README, an HF card badge, a lock-file assertion, or a STATUS sentence. Where a figure
could not be verified it is written **unverified**, never estimated. Golden bodies are
gitignored and live at `~/.memphant-private/`; they were read read-only.

---

## 0. Headline findings

1. **The flagship paid run is underpowered at its own preregistered resolution.**
   Phase 2 preregisters `d_min = 7pt` on 221 scored rows, "powered ~80% at ψ≈0.15"
   (`docs/superpowers/plans/2026-07-27-accuracy-first-program.md:256`). That ψ is an
   assumption no run supports. Under the exact test the packet itself names, n=221 gives
   **72.8% power at ψ=0.15**, 68.0% at the median ψ of the 80q arm pool (0.167), and
   **54.2% at the ψ actually observed on this very lane** (0.229). True MDE is 7.6–9.2pt;
   reaching 7pt needs n = 260–390. **Do not launch Phase 2 as specified.**

2. **A run with fewer than 6 discordant pairs cannot reject, at any effect size.** The
   two-sided exact binomial at α=0.05 has no rejection region until n_d ≥ 6
   (2 × 0.5⁶ = 0.031). This is the general form of the n≤12 tripwire reclassification and
   it retires more than the n=12 screens (§3.3).

3. **The Track R paraphrase bank should be certified; the bar was wrong, not the bank.**
   It fails exactly one of twenty checks — `leak_concentration_le_1_50` at 2.018× — and
   that bar was set **below the measured achievable floor of 1.79×**. On every other axis
   it strictly dominates the bank we actually ran (§4.2).

4. **"LongMemEval's split is deprecated upstream" is false as stated.** The only
   "deprecated" strings that exist are our own local filename and one repo sentence. This
   premise is currently blocking work for no upstream reason.

5. **Track R's entire measured ladder rests on a contaminated bank, and the bank that
   fixes it has never been run.** Every banked b/c cell sits on a bank leaking 3.93×.

6. **Three of seven memory types have no banked paired result at all** —
   preference/user-learning, procedural, and temporal/state. Four do: chat, code, docs and
   forgetting. Substrate coverage is thinner still (§5), and **hot/cold planes have neither
   an instrument nor a feature.**

7. **Three external instruments have failed at first contact on our own side, never on
   theirs** — STATE-Bench, STALE and MemSyco. In each case the upstream data and scorer were
   fine and materialised; our adapter was not. Two of the three were discovered only after
   money was authorized (§4.5). One $0 gate would have caught all three.

8. **Memora's headline "flat 43/71 vs 44/71" is not flat** — it hides 25 discordant cells
   out of 71 (§4.4). A one-cell net was reported for a change that moved a third of the
   graded cells.

---

## 1. How to read the leakage column

A leakage figure is a **five-tuple**, never a scalar, and is never comparable across banks
whose memory unit differs:

| field | what it settles |
|---|---|
| **unit definition** | what one memory row *is* (e.g. "user turn only" vs "user turn + agent reply") |
| **absolute target coverage** | how much of the query's vocabulary the target already contains |
| **floor** | the same measure against a non-target *in the same scope* (exhaustive or sampled — say which) |
| **concentration** | target ÷ floor — lexical tractability |
| **provenance class** | who authored the query, and could it have been written *from* the target |

Two independent axes, routinely conflated:

- **Provenance** settles **contamination** — was the query authored from the target? A
  machine-emitted CI log, or a reviewer comment written against the *pre-change* hunk,
  cannot copy a fix that does not yet exist, whatever its ratio.
- **Concentration** settles **lexical tractability** — can BM25 win without memory? A bank
  can be provenance-clean and still highly tractable. That is not disqualifying; it means
  the bank can only measure the lexical regime.

Track R original was **both** contaminated and tractable, which is exactly why it made
dense retrieval look worthless.

**Exhaustive vs sampled floor is not cosmetic.** The same Track R bank reads 3.9286×
against the exhaustive floor and 4.1905× against the sampled floor
(`track_r_paraphrase_golden.lock.json:leakage.reference_original_bank`). Always name which.

### The unit definition alone can flip a verdict

Same pinned `scripts/track_r_leakage.py`, same conversation bank, two unit definitions:

| unit | target coverage | floor | concentration |
|---|---:|---:|---:|
| user turn + agent reply (shipped) | 0.3367 | 0.2246 | 1.4991× |
| user turn only | 0.1871 | 0.1370 | 1.3657× |

The agent's reply restates the user's vocabulary and therefore sits on **both sides** of
the metric — inside the target *and* inside the non-target floor — inflating both. Shipped
figure verified at `~/.memphant-private/convo-lane/convo_lane_leakage.json`
(`concentration_vs_exhaustive = 1.4991`, `concentration_vs_sampled = 1.5466`).

### Calibration procedure — adopt before the next preregistration

Three of our bars have now failed by being set below what reality achieves: the paraphrase
bar at ≤1.50×; `swe-prbench` — a *published human corpus* — failing our GitHub-lane gate at
2.42×; and five conversation-lane rows failing partly on a unit artifact. **A bar a real
human corpus cannot pass is measuring our metric, not our data.**

Two independent methods now converge on the achievable floor:

- **W0 direct probe** — hand-written, maximally paraphrased, still-answerable and still
  uniquely-identifying questions: **concentration 1.79** (n=27, target 0.131, floor
  0.0732). Verified at `~/.memphant-private/track-r-paraphrase/floor/floor-probe.json`.
- **Independent human corpora:** 1.76–2.03×.

The probe's own recorded roughness is kept: small sample, a single generator model wrote
every probe question, and uniqueness is agent-judged. Treat 1.79 as an order-of-magnitude
floor, not a constant. Note also that it is **shape-dependent** — the probe's `state-churn`
cell reads 0.9134× (target coverage *below* the random-non-target floor) against
`file-symbol-grounding` at 2.1717× — so a shape-uniform bar is itself a defect.

Procedure:

1. Fix the **unit definition** first and write it into the preregistration.
2. Measure the **achievable/human floor for that unit** — a W0-style probe costs $0, and a
   published human corpus (swe-prbench for code review) gives the independent check. The
   0.175–0.287 band applies only to human-authored *coding queries* and must not be reused
   across units.
3. Set the bar **relative to that measured floor**, per shape where shapes differ, and
   record both numbers in the prereg.
4. Report all five fields at gate time. A bank **below** the achievable floor is *harder
   than reality, not better*, and must be flagged rather than celebrated.

---

## 2. The register

Split into two halves sharing the same lane key. `n` is scored rows actually on disk.
"Ever run?" distinguishes **apparatus** (code exists) from a **banked result** (real
numbers in a committed artifact).

### 2A — identity and integrity

| # | type / substrate | instrument | license (verified) | pinned? | n | provenance class | leakage: target / floor / conc. |
|---|---|---|---|---|---:|---|---|
| 1 | episodic/chat | LongMemEval-S (`longmemeval-cleaned`) | **UNVERIFIED** — the lock carries no license field at all | yes, rev `98d7416c…` | 500 (178 dev-frozen / 238 exposed / 259 sealed) | human-authored upstream benchmark | n/a — upstream haystack construction |
| 2 | episodic/chat | LME-S 80q adversarial pool | as above | yes | 72 scored | ours, agent-mined from upstream | unverified |
| 3 | episodic/chat | Phase-2 paid reader QA | as above | yes | 221 planned | model reader, model judge | n/a |
| 4 | episodic/chat | LongMemEval-V2 state-aware | Apache-2.0, LICENSE blob pinned | yes | 0 materialised results | upstream web-agent trajectories | n/a |
| 5 | repo/code | Track R golden bank (original) | CC-BY-4.0 (corpus) | yes, `35455389…` | 180 | agent-generated from the target | 0.3960 / 0.1008 / **3.93×** (exh.) |
| 6 | repo/code | Track R **paraphrase** bank | CC-BY-4.0 (corpus) | yes, same corpus | 180 | agent-generated, terms withheld | 0.1346 / 0.0667 / **2.018×** (exh.) |
| 7 | repo/code | `coding_events_golden` | private (Syndai) | yes | 40 | mined from prod attempt events | unverified |
| 8 | repo/code | GitHub lane golden bank | mixed: CC-BY-4.0 public + private | yes | 416 mined, **13 bar-clearing** | S1 machine / P1 human reviewer / S4 bot | S1 3.31×, P1 2.42×, S4 2.11× |
| 9 | repo/code | SWE-ContextBench tranche 1 | unverified | yes | 12 targets | upstream | n/a |
| 10 | repo/code | SWE-Explore | MIT (lock), LICENSE blob **not** pinned | yes, `bdb0ae4…` | 848 rows, **0 usable** | upstream | n/a |
| 11 | semantic/docs | Syndai docs gate | private (Syndai) | yes, commit `6fe7f78f…` | 60 (+60 v2) | mined from the docs themselves | unverified |
| 12 | preference/user | Track U golden bank | private (owner's own files) | yes, sha recomputed & matching | 51 goldens (+60 probes) | owner-authored feedback files, human-curated | unverified |
| 13 | procedural | rung10 state-style replay | n/a — self-authored | n/a | **1** | self-authored | n/a |
| 14 | forgetting/lifecycle | ForgetEval adversarial-385 | MIT, blob `cdf599e8…` pinned inside the result artifact | yes, `b6053b7` | 385 cases, **259 scorable** | upstream template-generated | n/a |
| 15 | temporal/state | STATE-Bench v0.8.0 | MIT — true, verified by this audit; **lock does not bind it** | yes, `e2c8d7af…` / v0.8.0 | 450 tasks / 150 test / 300 train traj. (now verified) | upstream, split endorsed | n/a |
| 16 | temporal/state | Memora / FAMA | Apache-2.0, LICENSE blob pinned | yes, `a6493188…` | 71 subquestions in **15 parent questions** | upstream; 3-LLM-judge consensus | development-exposed persona |
| 17 | temporal/state | STALE | MIT / CC-BY-4.0 asserted as a bare string | yes, `617c51dc…` | upstream 400; pilot reached **4** | upstream | n/a |
| 18 | temporal/state | MemSyco | MIT, LICENSE blob pinned and recomputed | yes, `c31e2c85…` | upstream 1,550; our calibration splits **12–14** | upstream | n/a |

### 2B — evidence, power, cost

| # | instrument | ever run? | last result | ψ observed | MDE @80% | cost/run |
|---|---|---|---|---:|---|---|
| 1 | LongMemEval-S retrieval | **yes** | hit@5 baseline .614 → cap-1200 .843 (b=0, c=38, n=166) | 0.2289 | **10.7pt** — need n≥390 | $0 |
| 2 | LME-S 80q pool | **yes**, 21 arms | median arm b=4/c=8 | 0.1667 | **13.5pt** — need n≥285 | $0 |
| 3 | Phase-2 paid reader QA | **NO** — `paid_calls_executed: 0`, `authorization: null` | none | **unmeasured** | 7.6–9.2pt under proxy ψ | §7 |
| 4 | LongMemEval-V2 | **NO** — 9,405 harness lines, `official_output_files: 0` | none | unmeasured | — | §7 |
| 5 | Track R original | **yes**, 4 paired arms | fused vs scoped-BM25 @10: b=15, c=3 | 0.1000 | **6.7pt** (adequate) | $0 |
| 6 | Track R paraphrase | **NO** — zero scored arms | none | unmeasured | — | $0 to run |
| 7 | `coding_events_golden` | **yes** | BM25 vs MemPhant @10 Δ = −0.05 | ≥0.05 (bound) | **UNPOWERABLE at n=40** | $0 |
| 8 | GitHub lane | **NO** — fetch/extract/leakage/secrets scripts only, no runner | none | unmeasured | — | $0 to run |
| 9 | SWE-ContextBench | ran, **unpairable** | 3/4 no-memory baselines resolve; max gain 1 < required 2 | n/a | cannot express the effect | sunk |
| 10 | SWE-Explore | **NO** | none | unmeasured | — | blocked |
| 11 | Syndai docs gate | **yes** | hit@10 Δ = −0.133; QA Δ = −0.167 (MemPhant **loses**) | ≥0.133 (bound) | **13.1pt** — need n≥230 | $0 |
| 12 | Track U | **NO** — no runner exists | none | unmeasured | unpowerable below ψ=0.20; 17.4pt at ψ=0.20 | $0 to run |
| 13 | rung10 procedural | nominally | 1.0 vs 0.0, CI [1.0, 1.0], n=1 | n/a | not a measurement | $0 |
| 14 | ForgetEval | **yes** | 244/15/126 vs baseline 133/126/126; b=111, c=0, n=259 | 0.4286 | **11.7pt** — need n≥710 | $0 (deterministic scorer) |
| 15 | STATE-Bench | **NO** | none | unmeasured | — | §6.2 — $2,254–$10,704 working; **$211–634 broken** |
| 16 | Memora / FAMA | **yes**, 3 runs | FAMA 32.96 → 53.49; raw 44/71 → 43/71 but **b=13, c=12** | 0.3521 | **20.3pt**, and that is a floor (nesting) | prior runs settled ~$1.5–2 |
| 17 | STALE | apparatus ran, **no score** | 12/12 dimensions `insufficient`, `promotion_ineligible: true` | none | — | $0.42 already sunk, no result |
| 18 | MemSyco | **NO** — 2 official tracks retired at 0 samples | none | none | all splits n=12–14: unpowerable | $0.0015 sunk |

---

## 3. Power analysis

Computed, not asserted, by `scripts/instrument_power.py` against
**two-sided exact (conditional binomial) McNemar at α=0.05** — the test the lanes actually
name. Power is integrated **unconditionally** over N_d ~ Binomial(n, ψ); fixing the
discordant count at its expectation, the common shortcut, is optimistic at our n.

Validation of the calculator: at δ=0 it returns 0.0319 ≤ α (the exact test is
conservative, as it must be), and it reproduces the packet's own arithmetic.

### 3.1 The chat lane is the expensive mistake

`d_min = 7pt` on n=221:

| ψ | source | power at 7pt | true MDE | n needed for 7pt |
|---:|---|---:|---:|---:|
| 0.150 | **the packet's assumption** | 0.728 | 7.55pt | 260 |
| 0.229 | observed on this very lane (rung-7) | 0.542 | 9.24pt | 390 |

The preregistered claim of ~80% power is wrong at every ψ we have. The gap is not the
arithmetic — it is that **ψ for the reader endpoint has never been measured**. Retrieval
discordance is the only proxy we own, and its two candidates differ by 37%. Note the
direction of the risk: reader discordance is often *lower* than retrieval discordance,
which would help — but "often" is not a measurement, and the packet's ψ=0.15 sits below
both observed proxies, so the assumption errs in the one direction that matters.

**Reachability.** The scored pool is 221 now, 259 sealed, ≈480 maximum. n=390 is therefore
reachable *only by spending the sealed confirmation set inside the screening run*, which
destroys the confirmation. That is a design decision, not a budget line.

### 3.2 Every other lane

| lane | n | ψ | MDE @80% | verdict |
|---|---:|---:|---|---|
| Track R fused vs BM25 @10 | 180 | 0.1000 | 6.73pt | **ADEQUATE** for 7pt — the only lane that is |
| Track R rank-order @10 | 180 | 0.1222 | 7.49pt | inadequate; need n≥210 |
| Track R render-loss @5 | 180 | 0.1944 | 9.51pt | inadequate; need n≥330 |
| ForgetEval cross-rerank | 259 | 0.3127 | 10.02pt | inadequate for 7pt; need n≥525 |
| ForgetEval lineage-complete | 259 | 0.4286 | 11.67pt | inadequate for 7pt; need n≥710 |
| ForgetEval transition-safe *(retracted)* | 259 | 0.6371 | 14.18pt | inadequate; need n≥1045 |
| Syndai docs hit@10 | 60 | ≥0.1333 | 13.08pt | inadequate; need n≥230 |
| Syndai docs QA | 60 | ≥0.1667 | 14.67pt | inadequate; need n≥285 |
| Memora pilot vs replay | 71 | 0.3521 | ≥20.3pt | inadequate; **MDE is a floor** (nesting) |
| Track U | 51 | unmeasured | 17.4pt at ψ=0.20 | **UNPOWERABLE** below ψ=0.20 |
| `coding_events_golden` @10 | 40 | ≥0.05 | — | **UNPOWERABLE** |
| LME-S non-regression | 166 | 0.0000 | — | **UNPOWERABLE** (b=c=0 by construction) |
| LME-S `_abs` sentinel | 12 | 0.1667 | — | **UNPOWERABLE** (n_d=2) |
| MemSyco (all 5 splits) | 12–14 | unmeasured | — | **UNPOWERABLE** (below the n_d≥6 floor) |
| procedural rung10 | 1 | — | — | not a measurement |

**Higher discordance costs power.** ForgetEval has the largest effect we own (+42.9pt) *and*
the worst resolution (11.7pt), because ψ=0.43 puts noise in 43% of pairs. Lanes are not
ranked by how big their wins look.

Two results worth stating plainly:

- **The docs-lane negative result is sound.** Δ = −13.3pt (hit@10) and −16.7pt (QA) both
  exceed their own MDE of 13.1/14.7pt. The C2 drop rests on adequately-powered evidence.
  This is the only lane where a *decision* was correctly supported by its instrument.
- **The plan's coding-bank sizing is correct.** At the observed Track R ψ=0.10: n=100 →
  8.84pt, n=150 → 7.34pt, n=180 → 6.73pt, n=200 → 6.40pt. The plan's "~10pt floor at 100
  goldens, 150–200 for ~7pt" (`:199`) is right, if slightly conservative. Keep the target.

### 3.3 Underpowered historical conclusions — the full list

The n≤12 screens are already reclassified as tripwires. The exact test says the rule is
sharper and catches more:

**Structural floor: n_d ≥ 6.** Below six discordant pairs there is no rejection region at
all, so power is *zero for any effect size*. Consequences:

- **n=12 screens** (`forgeteval.next-evidence.n12.json`, `lme_s.packing-pilot.n12.json`,
  `longmemeval_v2.packing-kill.n12.json`, `swe_contextbench.kill.n12.json`): at a generous
  ψ=0.30 the probability of even reaching six discordant pairs is 0.118. Correctly
  reclassified — the reclassification was if anything too generous.
- **The 2-discordant-pair packing screen** (plan `:96–97`, exact p=0.5): already recorded
  as zero-power. Confirmed structurally, not just numerically.
- **NEWLY FLAGGED — `coding_events_golden`, n=40.** Unpowerable at ψ≤0.15, and even at
  ψ=0.30 its MDE is 24.2pt. Its held-out slice is 4 questions. **No conclusion drawn on
  this bank at any k is defensible**, including the −0.05 BM25 comparison.
- **NEWLY FLAGGED — `Track R render-loss @5` and `rank-order @10`** were reported as wins
  (b=35/c=0 and b=22/c=0). Both are individually significant by the exact test, but their
  MDEs (9.5pt, 7.5pt) exceed the 7pt resolution the program claims to work at, *and* both
  sit on the contaminated bank. Direction is safe; magnitude is not.
- **NEWLY FLAGGED — the b=c=0 non-regression pairs** (phase1d, phase1w, and the p1r-small
  arm). These are valid as non-regression statements and **invalid as evidence of
  equivalence** — with zero discordant pairs the test has no power to detect any
  difference. Do not cite them as "no difference".

---

## 4. Verdicts — verified against shipped rows

Verdict key: **SOUND** = usable as-is for a decision · **DEGRADED** = usable only with a
named restriction · **BROKEN** = must not carry a decision · **ABSENT** = no instrument.

### 4.1 Episodic / chat

**LongMemEval-S (`xiaowu0162/longmemeval-cleaned`, rev `98d7416c…`) — DEGRADED.**

- *Deprecation premise is false.* Both upstream pages were fetched fresh: no deprecation
  notice, no archive banner, and V2 is presented as an additional benchmark rather than a
  successor. The only "deprecated" strings anywhere are our own local filename
  `~/.memphant-private/longmemeval-cleaned/longmemeval_s_original_deprecated.json` and one
  sentence at `docs/build-log/2026-07-31-w2-reader-composition-prereg.md:51`. That sentence
  is blocking W2.1 on a premise with no upstream basis. **Correct it and unblock.**
- *What "cleaned" actually changed:* 454 of 500 questions differ from the original, and the
  diff is **pure distractor deletion** — zero question, answer, or label edits. We are
  pinned on the cleaned side, which is the defensible side. The change is real but benign.
- *License: **UNVERIFIED**.* `benchmarks/manifests/longmemeval_s.lock.json` carries no
  license field at all, unlike `longmemeval_v2.lock.json` and `memsyco.lock.json`, which
  both pin the LICENSE blob's sha256. This is the one instrument we lean on hardest and the
  one whose license we never checked.
- *The @10 metric is dead on our slice.* Recomputed directly from the 178 `per_question`
  rows in both arms: the maximum `first_answer_rank` ever observed is **5**, so `hit@10` is
  identical to `hit@5` on all 166 scored rows in both arms. Any result we have ever reported
  "at k=10" on this slice is the k=5 result relabelled.
- *Saturated above k=16* on the 80q pool (base recall@16 = 0.986, recall@48 = 1.000). The
  band where reranking can express anything is k ≤ 16.

**The `_abs` sentinel that killed `pack_render_cap` — BROKEN, confirmed structurally.**
Recomputed independently: n=12, b=0, c=2, exact two-sided p = 0.50. With n_d = 2 against a
structural floor of 6, it could not have rejected at *any* effect size. The Phase 0
rescission is now supported by the test's own arithmetic, not only by the trap-session
argument.

**Phase-2 paid reader QA — apparatus only, never run.**
`authorization-request.v3.json` records `paid_calls_executed: 0`, `settled_cost_usd: "0"`,
`authorization: null`. `scripts/derive_phase2_packet.py --check` reproduces the packet
byte-identically, so the *ceiling* is sound; the *power note* inside it is not (§3.1).

**LongMemEval-V2 state-aware — BROKEN as a going concern.** 9,405 lines of harness with
`official_output_files: 0` and `settled_micros: 0`; manifest versions v1/v3/v4 are marked
`ABANDONED_NEVER_RESUME`; all 11 `p1-t6` runs carry an `INVALIDATION-PROOF.json`. This is
the largest single block of apparatus in the repo with no banked result. The plan already
parks it (Phase 4); the register agrees and adds: **do not resume without first fixing the
0/848 SWE-Explore dependency and re-deriving the packet.**

### 4.2 Repo / code

**Track R original bank — BROKEN for magnitude, usable for direction only.**
`benchmarks/data/track_r_repo_memory_golden.lock.json` records `bar_passed: false`: 14 of
15 checks pass but `with_distractors_ge_50pct` fails, because only 75 of 180 goldens
(41.7%) have adjudicated distractors. Leakage is target 0.3960 / floor 0.1008 / **3.9286×
exhaustive** (4.1905× sampled) against an achievable floor of 1.79×. Provenance class:
agent-generated *from the target*, so the question can copy the answer's vocabulary. **All
four banked paired arms sit on this bank.** Their direction is credible; their magnitude is
not a memory effect.

**Track R paraphrase bank — SOUND; the bar is what failed, not the bank. Certify it.**

| check | original | paraphrase |
|---|---:|---:|
| adjudicated distractors | 75/180 (41.7%) | **180/180 (100%)** |
| mean lexical overlap | 0.0517 | **0.0162** |
| target coverage / floor / concentration | .3960 / .1008 / 3.93× | **.1346 / .0667 / 2.018×** |
| mean withheld terms | — | 19.45 |
| bar checks passed | 14/15 | **19/20** |

Its single failing check is `leak_concentration_le_1_50` at 2.018×. That bar sits **below
the measured achievable floor of 1.79×** and below the independent human band of
1.76–2.03×; 2.018× is inside that band. The bank is as clean as this construct can be made.
Two governance defects follow, both to be fixed in the prereg, not in the data:

1. The bar was never calibrated against an achievable floor (§1).
2. The bar is inconsistently cited: the paraphrase lock records failure against **1.50×**
   while the GitHub-lane preregistration cites the same standard as **2.05×**. One number,
   two values, both in force. Pick one, in writing.

**`coding_events_golden` — BROKEN.** n=40 drawn from 8 attempts in a **single repo**; the
held-out slice is 4 questions. Unpowerable at ψ ≤ 0.15 and 24.2pt MDE even at ψ=0.30. No
conclusion on this bank is defensible, including the −0.05 BM25 comparison.

**GitHub lane — DEGRADED, and its metric is mis-specified.** 416 goldens mined; three of
five preregistered bars fail; the bar-clearing slice is 13 goldens against a ≥40 floor. The
recorded mis-specification is correct and important: concentration detects **copying**,
which requires the query to be writable from the target. S1's query is emitted by CI
*before the fix exists* and P1's by a reviewer against the *pre-change* hunk — neither can
copy. A high ratio there is causal specificity, not contamination. That a published human
corpus (`swe-prbench`, CC-BY-4.0) fails our gate at 2.42× is the proof. There is also **no
runner**: `scripts/github_lane_*.py` are fetch, extract, leakage and secrets only.

**SWE-ContextBench tranche 1 — BROKEN (saturated).** 3 of 4 no-memory baselines resolve, so
the maximum achievable gain is 1 against a required 2. The instrument arithmetically cannot
express the effect. Terminal for this tranche.

**SWE-Explore — BROKEN.** Of 848 shipped rows at pinned rev `bdb0ae4…`, `problem_statement`
is populated on **0** and `base_commit` on **0**, despite the upstream README. The lock's
own `observed_release_gaps.base_commit_rows: 0` already recorded this. Its MIT license is
asserted in the lock without a pinned LICENSE blob — unverified.

### 4.3 Semantic / docs

**Syndai docs gate — SOUND, and the only lane whose decision its instrument could
support.** n=60 paired. hit@10 Δ = −0.133, QA Δ = −0.167; **MemPhant loses to Syndai's own
stack on both**. Both effects exceed their own MDEs (13.1pt, 14.7pt), so the C2 drop rests
on adequately-powered evidence. Two restrictions: only `b − c` is recoverable because the
artifact commits a bootstrap CI rather than the two discordant cells (so ψ is a lower
bound — **commit b and c from now on**), and at n=60 the lane cannot resolve anything
smaller than ~13pt in future.

### 4.4 Temporal / state, forgetting, procedural, preference

**STATE-Bench v0.8.0 — BROKEN. Do not authorize a paid run.**

This is the instrument the owner specifically asked us to confirm before paying for it. It
would not have worked, and the failure was reproduced live at $0.

- *Nothing was ever materialised here.* `benchmarks/state_bench/` is 4.0K holding one
  90-line file. The upstream cache dir does not exist, and a filesystem-wide search for the
  pinned revision, `compute_metrics.py`, or any trajectory file returned zero hits. Until
  this audit, **every "450 tasks / 300 train trajectories" figure in the lock was an
  unbacked transcription.**
- *Now verified.* A fresh $0 clone at tag `v0.8.0` has HEAD `e2c8d7af…` — exactly the
  pinned revision — and the repo's own auditor
  (`scripts/run_state_bench.py --dry-run`) returns `audit-ok-no-model-calls` with 50 test
  tasks per domain. That transitively verifies all four native-scorer sha256s, the
  aggregate inventory hash, and 450 tasks / 150 test / 300 train trajectories. **n is
  real, and the split is upstream-endorsed.**
- *License: true but unbound.* The checkout has a real MIT `LICENSE`
  (sha256 `2e969379…`). The claim is correct — but `state_bench.lock.json` asserts it as a
  bare string with no blob hash, unlike `memsyco`, `longmemeval_v2` and `memora`, which all
  pin the LICENSE file. `stale.lock.json` has the same weakness. It happened to be true
  this time; the lock does not make it checkable.
- *It would fail on every single retrieval call.* `memphant_memory_agent.py:31-38` POSTs
  `tenant_id` to `/v1/recall` and omits `subject_id`, `agent_node_id` and
  `subject_generation`. The landed strict contract
  (`crates/memphant-types/src/lib.rs:1778-1794`) is `#[serde(deny_unknown_fields)]` and
  requires all three. The adapter predates the C0 strict-contract migration that killed
  `tenant_id` and was never re-exercised. `build_state_bench_memphant_arm.py:398-402`
  already writes the correct ids into the runtime config; the agent reads the wrong keys
  out of a config that contains the right ones.
- *Reproduced, not inferred.* Loaded through the official
  `load_root_agent_class` loader and called at the official tool entry point against a stub
  enforcing the real serde semantics: `RuntimeError: MemPhant recall failed: HTTP 400`.
  Control: patching only that payload block returns a correct learning. The rest of the
  adapter — degraded check, top-3 guard, body extraction, proof append — is sound.
- *Blast radius if purchased.* The exception propagates to `run_batch.py:168`.
  `--retry-attempts` defaults to 3, so every task is billed three times; `traj.save()` is
  never reached, so no trajectory is written for any task; `verify_results` then refuses to
  aggregate. **150 test tasks × 5 runs × 3 retries, purchased, zero scored rows.**
- *Ever run?* No. Zero banked results, no artifact anywhere. STATUS.md:196 is honest on
  this one.
- *Cost per run:* **unverified** — with no tasks, envs or trajectories ever materialised
  here, the byte-measured derivation had no input. Any dollar figure quoted for STATE-Bench
  before now was a guess.

**ForgetEval — SOUND, and the one STATUS claim that checks out exactly.**
Recomputed from the 385 shipped case rows rather than the sentence: baseline 133 pass /
126 fail / 126 N/A, lineage-complete 244 / 15 / 126, with the **same** 126 N/A rows in both
arms (agreement 385/385). Paired base n=259, **b=111, c=0** — precisely the claimed "111
paired gains and zero baseline regressions". The +42.9pt effect sits far above the lane's
11.67pt MDE, so the banked result is comfortably powered.
Cross-validated independently: `forgeteval.next-evidence.n12.json` states the cross-rerank
arm "repaired 65 failures but regressed 16 baseline passes", matching the recomputed cells
(b=65, c=16) exactly. And the **retracted** 188/71/126 arm was retracted for a good reason
the prose never gave: it carries **55 baseline regressions** that the lineage arm
eliminates entirely.

*Restrictions, both material:*
- ψ = 0.43 is the highest of any lane we own, so ForgetEval needs n ≥ 710 to resolve 7pt.
  **Sound for large effects, unusable for fine tuning.**
- **32.5% of a forgetting benchmark is permanently unscorable for us.** All 126 N/A rows
  are `error_kind: not_supported` — 125 of them the *entire purge family* — because
  MemPhant has no selective hard purge-by-query primitive. And in the lineage arm amnesia
  (53/53) and decay (21/21) are **fully saturated**. What remains scorable and unsaturated
  is drift and supersession.
- The official corpus is **not on disk**; re-running requires re-cloning. Labels are
  upstream template-generated with a deterministic literal substring scorer — no judge
  model, hence $0.
- Not SOTA: the raw score ties Lethe v1 and trails Mem0 and the published LLM-assisted arms.

**Memora / FAMA — SOUND as an instrument, DEGRADED as evidence. And "flat" was not flat.**

Apache-2.0 verified against a pinned LICENSE blob; official code, data and native scorer;
every STATUS number reproduces exactly from the scorer outputs.

**The most consequential misreading in the ledger.** STATUS reports that after the
reader-only replay "raw unweighted accuracy stayed flat at 43/71 vs the pilot's 44/71".
Matched by `evaluation_question_id`, that one-cell net hides **25 discordant cells out of
71** — b=13 the pilot alone got right, c=12 the replay alone did (ψ = 0.352). The replay is
not a near-identical run with one extra miss; **it is a different system that changed a
third of the graded cells.** Any power estimate treating the 32.96 → 53.49 move as a
one-cell delta is wrong by more than an order of magnitude.

*Carry this caveat with the number:* the 71 subquestions nest inside **15 parent questions**
(mean 4.7 each) and are not independent, so an exact McNemar at n=71 is **anticonservative**
— the effective n is nearer 15. The 20.3pt MDE in §3.2 is therefore a *floor* on the true
MDE, not the MDE.

Two further degradations:
- **Development-exposed, not a holdout.** The run is a single sealed persona
  (weekly/software_engineer), 15 questions, against which the build log records repeated
  targeted prompt and extractor iteration across 2026-07-14.
- **The judges disagree.** Grading is a strict three-LLM consensus, and per-judge overall on
  the pilot is 44 / 46 / 44.
- A **fourth, newer result** exists — FAMA 61.67, 56/71, in
  `docs/build-log/2026-07-15-memora-causal-split.md` — that the STATUS 53.49 sentence does
  not carry, and no `.fama.json` was located for it. **Unverified.**

**STALE — BROKEN. Two authorizations consumed, $0.42 settled, zero official score.**
- `AUTHORIZATION-1-CLOSURE.json`: `CLOSED_PREPAID_OPERATIONAL_FAILURE` — the adapter passed
  an upstream **timezone-naive timestamp** to MemPhant and the first episode was rejected.
- `AUTHORIZATION-2-CLOSURE.json`: `CONSUMED_REJECTED_STOP_NO_BROADENING`, with
  `native_judge: NOT_RUN_KILL_GATE` and `full_400_scenario_run: FORBIDDEN_NO_BROADENING`.
  Settled $0.4245304, and `deep_cost_status` is `UNRECONCILED`.
The pilot reached n=4 records / 12 probe dimensions and
`run-2/current/proof.json` records **12/12 dimensions `insufficient`** with
`answer_policy: abstain_unknown` and `promotion_ineligible: true`; the candidate arm never
reached recall at all. The upstream 400-record dataset and native scorer are materialised
and pinned — **the MemPhant side is what fails.** No official STALE number exists. The lock
also asserts its licenses as a bare string with no LICENSE blob.

**MemSyco — DEGRADED: the best-prepared instrument in the set, and it has never produced a
number.** MIT verified end to end (the lock pins LICENSE `f607254f…`, recomputed against
the materialised upstream and matching). Upstream is 1,550 samples, verified by line count
and exactly matching the lock. Predeclared SOTA gates and a 10,000-resample bootstrap
protocol are sealed and ready.

But two official tracks are explicitly retired with **zero completed samples**:
`objective/OFFICIAL-RETIREMENT.json` records `memphant_completed_samples: 0` with
`failure_class: structured_extractor_evidence_grounding`, and `scope/OFFICIAL-RETIREMENT.json`
records `memphant_answers: 0, memphant_judges: 0, memphant_report_exists: false`, retired on
the first fail-closed rejection for $0.0015. **Same failure class as STALE**: MemPhant's
structured extractor rejects on evidence grounding before any answer exists.

Two further cautions. The committed 3.9M under `benchmarks/memsyco/` is **not upstream
data** — it is MemPhant-generated calibration packets. And **every one of the five
calibration splits is n = 12–14**, so by §3.3's structural floor not one of them can reject
at any effect size. As instrumented, MemSyco is entirely tripwires.

**Procedural — ABSENT. n = 1.** The only apparatus is
`rung10-procedural-memory-profile.json`, self-authored (`sampled_public_style`), saturated
at score 1.0 against baseline 0.0 with a CI of **[1.0, 1.0]** — and its trace
(`rung10-state-style-sampled-traces.json`) contains `total_cases: 1`. One self-authored
case. A CI of [1.0, 1.0] on n=1 is not a measurement, and STATUS:172 has already reopened
rung 10 on the grounds that its promotion evidence was synthetic fixtures. The only real
procedural instrument in the register is STATE-Bench's `retrieve_learnings` axis — and
STATE-Bench is BROKEN. Track U's 34 procedural rows are the nearest usable substitute, and
they have never been run.

**Track U (preference/user-learning) — apparatus without a runner, and a stop condition.**
The bank is real and well-constructed: 51 goldens accepted from 60 candidates over 91
feedback files, pinned source snapshot, three axes, category weights within 1.7pt of target.
But the only script is `user_lane_extract.py` — **there is no runner**, and no artifact
anywhere references the lane. Worse, the ceiling is structural: at n=51 the lane is
**UNPOWERABLE below ψ=0.20** and resolves only 17.4pt at ψ=0.20. Deciding 7pt would need
n = 340 (ψ=0.20) to 665 (ψ=0.40) against a pinned source snapshot of **94 files total**.
**That n is unreachable on the available corpus.** Track U can honestly be sized for a
~15pt effect (n=72 at ψ=0.20) and nothing finer. This is a stop condition, not a budget
request.

### 4.5 The systemic finding: adapters are not exercised before money is authorized

**Three** external instruments have now failed at first contact on our own side, never on
theirs. In every case the upstream data and scorer were fine and materialised:

| adapter | defect | discovered | cost of discovery |
|---|---|---|---|
| STATE-Bench | POSTs `tenant_id`, omits three required fields, against a `deny_unknown_fields` contract | by this audit | $0 — before spend |
| STALE | passed an upstream timezone-naive timestamp; first episode rejected | after authorization | a consumed prepaid authorization |
| MemSyco | structured extractor rejects on evidence grounding before any answer exists | after authorization | two official tracks retired at 0 samples |

STALE and MemSyco share a failure class. STATE-Bench's is a stale contract: the adapter was
written against the contract of the day, C0 then killed `tenant_id`, and nothing
re-exercised the adapter until money was on the table. Its remediation is **one line out,
three lines in** at `benchmarks/state_bench/memphant_memory_agent.py:33` — the builder
already writes all five correct ids into the runtime config via `gate.bind_context`, so no
builder change is needed.

**Recommended gate — no paid authorization for any lane whose adapter has not completed a
$0 stub-server round trip against the current strict contract since the last contract
change.** That check takes minutes. It would have caught all three, and it is the single
highest-leverage governance change in this register.

## 5. Serving substrates

| substrate | instrument | valid? | correct as shipped? | n | ever run / banked | verdict |
|---|---|---|---|---:|---|---|
| Postgres runtime — latency | `crates/memphant-store-postgres/tests/hot_path_slo_pg.rs` | yes | yes, and CI runs it | 80 samples | continuous in CI | **SOUND** |
| Postgres runtime — HTTP boundary | `scripts/episodic_lane_run_memphant.py --slo-samples` | yes | raises on breach | 200 samples | p50 **32.59ms** / p95 **37.18ms**, `artifacts/c1-episodic/slo-bar1-http-provenance.json`, 2026-07-22 | **DEGRADED** — not in CI |
| Postgres runtime — RLS | `crates/memphant-store-postgres/tests/served_path_rls.rs` | yes | yes, incl. a negative control | 2 tests + 4 corroborating | continuous in CI | **SOUND** |
| Store divergence (core trait) | `crates/memphant-store-testkit/src/lib.rs` via both contract suites | yes | 18 cases, byte-identical on both stores | 18 | continuous in CI | **SOUND** |
| Store divergence (file plane) | — | — | — | — | — | **ABSENT** |
| File plane B2/B3 | `crates/memphant-cli/tests/file_plane_n12.rs`, `crates/memphant-mcp/tests/distribution_wedge.rs` | yes | runs every push, 19 named properties | 12 + 8 | `artifacts/b2-file-plane/gate-summary.json`, `artifacts/b3-distribution-wedge/gate-summary.json`, 2026-07-23 | **DEGRADED** — InMemory only |
| MCP surface | `crates/memphant-mcp/tests/mcp_schema_contract.rs` + `scripts/e2e_probe.sh` | yes | real drift test vs committed artifacts | 7 tools / 4 templates | continuous in CI | **SOUND** |
| Hot/cold planes | — | — | — | — | — | **ABSENT — and the feature does not exist** |

Cost per run for every substrate instrument: **$0**. No paid model call appears anywhere in
`.github/workflows/ci.yml`.

### 5.1 Two standing notes are stale — stop repeating them

**"The server runs as superuser, so RLS is bypassed on the served path."** No longer true.
`memphant_migrations/versions/20260730_004_served_login_roles.sql` ships the login roles,
and `served_path_rls.rs` catches a regression from the outside: it mints a throwaway
NOINHERIT login that is a member of `memphant_app` and nothing else, then asserts on the
store's own pool that `current_user = memphant_app`, `NOT rolsuper`, `NOT rolbypassrls`,
`row_security_active`, cross-tenant count **0 in both directions**, and own-tenant count
**1** — so the zero is isolation, not blanket denial. It then runs a **negative control**: a
bypassing credential must return 1, on the stated grounds that if it ever returns 0 the
first assertion proves nothing. That control is the part most such tests omit. **SOUND.**

**The `claim_reflect_jobs` lane-split race.** Fixed in shipped SQL. A **blocking**
`pg_advisory_xact_lock` runs as its own statement inside the candidate loop *before* the
claim, with lanes ordered deterministically and the lock held to transaction end
(`memphant_migrations/versions/20260724_003_worker_claim_throughput.sql:128`). The comment
at :82–104 documents why both insufficient designs were rejected. No `try`-based gate
remains on the claim path; the residual `skip locked` uses are secondary row locks
underneath the advisory lock. Pinned by `tests/test_wsa_migration_contract.py:819`.

### 5.2 Substrate defects worth acting on

1. **Hot/cold planes do not exist.** No `ColdTier`, `hot_tier`, or tiering module anywhere
   in `crates/`; every `hot_*` symbol is `hot_path` latency. The only in-repo statement of
   fact calls the tiering **dormant**
   (`docs/build-log/2026-07-22-b5-b6-ci-honesty-deletions.md:121`). Any STATUS sentence
   claiming a hot/cold plane is false. Delete the claim or build the feature; do not audit
   it again.
2. **The file-plane write path is InMemory-only**, so the store-divergence anti-pattern is
   live and unguarded exactly where we have no differential. `file_sync` is not among the
   18 shared testkit cases. Fix: add `file_sync` cases to `memphant-store-testkit`.
3. **`b3-distribution-wedge/gate-summary.json` overclaims.** It records
   `"real_mcp_stdio_and_postgres": true` for a suite that constructs only `InMemoryStore`;
   that property is carried by the separate `scripts/e2e_probe.sh`, not by these tests. Its
   `"tested_commit"` is also the literal unsubstituted placeholder `"containing_commit"`,
   so the artifact is not commit-pinned the way B2's is.
4. **The acceptance-grade SLO number is manual.** Only the store-layer proxy runs
   continuously; the HTTP-boundary instrument that produced p50 32.59 / p95 37.18 is not
   in CI.
5. Both file-plane summaries self-report `repository_gate_status: unmet` (24 pytest
   failures at bank time; private spec-drift parity unproven). The gates passed; the
   repository gate did not. That honesty is correct and should stay.

### 5.3 The `#[ignore]` sweep came back clean

92 ignored Rust tests: 65 are the Postgres/worker suites that CI runs explicitly via
`cargo test … -- --ignored` (`.github/workflows/ci.yml:134-138`), and the rest are gated on
large model downloads or paid provider keys. No `#[should_panic]` misuse, no always-true
assertions, and no test gated on an env var that CI silently never sets. All four B6
honesty legs are present and green (latest run 2026-07-28, both jobs success).

## 6. The spend plan

Ranked by **decision-value per dollar**. Ceilings follow the `derive_phase2_packet.py`
convention: one-byte-per-prompt-token liability at the **widest measured** row, 1024
completion tokens, priced at the recorded provider maxima, times an enumerated call
budget. Where an input has never been measured, the ceiling is marked **not derivable**
and the $0 measurement that would unlock it is named. No figure here is estimated.

### 6.0 Spend nothing until these $0 items are done

They are prerequisites, not preliminaries — each one changes what a paid run would buy.

| # | $0 action | what it unblocks |
|---|---|---|
| Z1 | **Run the Track R paraphrase bank** through `scripts/track_r_retrieval_arm_compare.py` | The entire repo/code ladder currently rests on a 3.93× contaminated bank. This is free, and it is the single highest-value action in the program. Preconditions verified: 180 rows, sha matches the lock, exactly one provenance span per golden (the runner's hard requirement at `:85-87`), zero blank spans, zero abstentions. Not executed — verified as runnable. |
| Z2 | **Re-run the code-lane retrieval arm with `--emit-qa`** | Banks the packed evidence rows. Without them the Phase 3 ceiling is **not derivable** (§6.1). The chat lane already did this; the code lane did not, and its per-question outputs were gitignored. |
| Z3 | **Fix the STATE-Bench adapter payload** | One payload block. Without it a paid run bills three times per task and writes zero trajectories (§6.2). |
| Z4 | **Re-preregister the chat lane's `d_min`** at its true MDE, or restructure the pool | Phase 2 as written cannot deliver 7pt (§3.1). This is a governance edit, not a run. |
| Z5 | **Calibrate the leakage bar** against the measured achievable floor and reconcile 1.50× vs 2.05× | Two values of one bar are currently in force (§4.2). |
| Z6 | **Commit `b` and `c`, not just a bootstrap CI**, in every paired analyzer | Four lanes have only lower-bound ψ because the discordant cells were never written down. Cheap now, uncomputable later. |
| Z7 | **Correct the LongMemEval deprecation sentence** at `docs/build-log/2026-07-31-w2-reader-composition-prereg.md:51` | It blocks W2.1 on a premise with no upstream basis (§4.1). |
| Z8 | **Add `file_sync` cases to `memphant-store-testkit`** | Closes the one live store-divergence gap (§5.2). |
| Z9 | **Delete the hot/cold plane claim** wherever it appears | The feature does not exist (§5.2). |

### 6.1 Must spend to know anything — lanes with zero measurement

| rank | lane | ceiling | what it buys | what it decides |
|---|---|---|---|---|
| 1 | **Coding-lane paired reader QA (Phase 3)** on the *paraphrase* bank | **not derivable** — 1,440 logical calls is derived (3 arms × 180 goldens + judge + 2 paired rechecks), but the per-call bound needs Z2. For scale only, at the chat lane's measured 26,000-byte bound the ceiling would be $127.29; that width belongs to a different corpus and a different reader, so it is **not this lane's ceiling** | The first measured answer to *does MemPhant memory help a coding agent* | The whole coding lane. Kill gate: no paired win over BM25 → the substrate has not earned it |
| 2 | **STATE-Bench first run**, *after* the one-line fix | **$2,253.50 floor / $10,704.14 ceiling** — derived: widest measured row 27,281 B → 28,000 prompt-token bound, 1024 completion, $0.0938960/call, 24,000–114,000 calls (150 test tasks × 5 runs × 15 max agent turns × agent + user-sim + 2 judges). See the caveats below | The only temporal/state instrument with an upstream-endorsed split and a native scorer | Whether we have any external validity on state memory at all |
| 3 | Track R paraphrase reader QA | not derivable (needs Z2) | Conditional on Z1 showing the retrieval effect survives decontamination | Whether the retrieval gain is a reader gain |

Two pricing caveats that must be resolved before either row is authorized:

- The reader for Phase 3 is `claude-opus-5`. **No opus-5 price is recorded anywhere in this
  repo** — the only pinned maxima are terra's 2.75/16.5 per million in the v3 packet.
- STATE-Bench's protocol pins **gpt-5.4**, and **no gpt-5.4 price is recorded either**. The
  figures above use the terra maxima so the lane is comparable to the others; the gpt-5.4
  amount itself is **unverified**.
- STATE-Bench's per-turn tool-round count is **unverified**: `StateBenchAgent.act` loops
  while tool calls exist with no explicit cap, and the mean is unmeasurable without a run.
  The 15 in the derivation is the configured *maximum* agent turns, not a mean — which is
  why the range spans 4.75×.

Both prices must be pinned at authorization time, exactly as the v3 packet already requires
for its robustness arm.

### 6.2 Do not spend — a purchase that would buy nothing

**STATE-Bench as it stands would be a void run.** The shipped adapter fails on *every*
retrieval call (§4.4), and the failure mode is maximally expensive: `--retry-attempts`
defaults to 3, so each task is paid for three times; `traj.save()` is never reached, so no
trajectory file is written for any task; and `verify_results` then refuses to aggregate.
Because the failure lands on the *first* `retrieve_learnings` call, the bill is not the full
run — it is roughly 1–3 agent calls per attempt × 3 retries × 750 task-runs = 2,250–6,750
calls: **$211.27 to $633.80, for zero scored rows.** That is the number to put in front of
the owner. The correct spend today is **$0 and one payload fix** (Z3), after which the lane
moves to §6.1 rank 2.

### 6.3 Spend only to refine a number we already have

| lane | ceiling | honest assessment |
|---|---|---|
| **Chat-lane Phase 2** (`pack_render_cap`) | **$142.32** — 1,610 calls at $0.088396, derived and reproducing byte-identically via `derive_phase2_packet.py --check` | The *ceiling* is sound; the *design* is not. At n=221 it resolves 7.6–9.2pt, not the preregistered 7pt. Three honest options: **(a)** relaunch with `d_min = 9pt` and no extra spend; **(b)** grow the pool to 260–390 scored rows, which is only reachable by consuming the sealed-259 confirmation *inside* the screen — that buys 7pt and destroys the confirmation; **(c)** do not run it. Note what is already known for free: the retrieval effect is +22.9pt, unanimous, b=38/c=0. The paid run does not ask whether the cap helps retrieval; it asks whether that survives to the reader. |
| Chat-lane confirmation + robustness arm | not derivable — priced at authorization time by design | Conditional on a Phase 2 pass |
| LongMemEval-V2 (Phase 4) | retained maximum $15.18, **not recoverable by resuming** | Correctly parked. Blocked behind SWE-Explore's 0/848 defect |

### 6.4 Ranked verdict

1. **Z1–Z9 first. All $0.** Several of them change what the paid runs are worth.
2. **Phase 3 on a decontaminated bank is the only paid run with a clean decision behind
   it** — and it is not yet priceable. Z2 makes it priceable.
3. **Phase 2 is priceable but under-resolved.** Do not launch it at 7pt.
4. **STATE-Bench must not be purchased today at any price.**

---

## 7. Stop conditions

Lanes whose n can **never** reach adequacy on the available corpus. These are stop
conditions, not budget requests.

| lane | ceiling | why it cannot be fixed by spending |
|---|---|---|
| **Track U** | n=51; unpowerable below ψ=0.20, 17.4pt at ψ=0.20 | Deciding 7pt needs n = 340–665. The pinned source snapshot is **94 files total**. The corpus cannot produce that n. Track U is honestly sized for a **~15pt** effect (n=72 at ψ=0.20) and nothing finer — preregister it that way or not at all. |
| **`coding_events_golden`** | n=40, held-out 4, one repo | Unpowerable at ψ≤0.15. Retire it; the Track R paraphrase bank supersedes it. |
| **MemSyco as calibrated** | five splits at n=12–14 | Below the n_d≥6 floor. The *upstream* 1,550 samples are not the constraint — our calibration slices are. Re-slice or do not cite. |
| **SWE-ContextBench tranche 1** | max gain 1 vs required 2 | Arithmetically saturated. Terminal for this tranche. |
| **SWE-Explore** | 0/848 usable rows | No spend fixes absent upstream fields. |
| **ForgetEval purge family** | 125 of 385 cases permanently N/A | We have no selective hard purge primitive. This is a product gap, not a measurement gap — the instrument is correct to refuse. |
| **Procedural** | n=1 | Not a measurement. The lane has no instrument; STATE-Bench is the only candidate. |

## 8. Consolidated integrity findings

**Licenses asserted as a bare string with no LICENSE-blob sha256** — the ClawArena badge
pattern. The assertion may well be true; the lock simply does not make it checkable:
`state_bench.lock.json` (MIT — independently verified true by this audit, sha256
`2e969379…`, but still unbound), `stale.lock.json`, `swe_explore.lock.json`,
`swe_contextbench.kill.n12.json`, `github_lane_golden.lock.json`,
`track_r_repo_memory_golden.lock.json`. The last two are HuggingFace dataset-card metadata
rather than a repo LICENSE blob and were **not** re-fetched, so they are unverified rather
than wrong.

**No license field at all:** `longmemeval_s.lock.json` — our most-used instrument.
(`user_lane_golden`, `coding_events_golden` and `syndai_docs_*` are private data; defensible.)

**Correctly bound, and the pattern to copy:** `longmemeval_v2.lock.json`,
`memsyco.lock.json`, `memora.lock.json`, `deep_swe.pairing.audit.json`. ForgetEval binds its
license inside the *result* artifact rather than a lock — unusual, but bound.

**Worktree hazard.** 87 of the 88 entries in
`docs/build-log/artifacts/canonical-artifact-allowlist.txt` are **absent from this
worktree**. All Memora and MemSyco evidence survives only in `/Users/sidsharma/Memphant`.
Any audit run inside a worktree will conclude "never run" for lanes that did run. This
register was checked against the main worktree for exactly that reason; future audits must
do the same, and the allowlist should say so.

**Report `b` and `c`, always.** Four lanes published only a delta or a bootstrap CI, so
their ψ is only a lower bound and their power is now unrecoverable. Two discordant cells
cost nothing to write down and cannot be reconstructed later.
