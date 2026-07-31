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
   **72.8% power at ψ=0.15**, 76.4% at the most favourable ψ we have ever observed
   (0.139), and **54.2% at the ψ actually observed on this very lane** (0.229). True MDE
   is 7.3–9.2pt; reaching 7pt needs n = 240–390. **Do not launch Phase 2 as specified.**

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

6. **Four of seven memory types have no banked paired result at all**, and several carry
   apparatus that has never executed once. Substrate coverage is thinner still (§6).

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
| 12 | preference/user | Track U golden bank | private (owner's own files) | yes | see §5 | owner-authored feedback files | see §5 |
| 13 | procedural | — | — | — | — | — | — |
| 14 | forgetting/lifecycle | ForgetEval | see §5 | yes | see §5 | see §5 | n/a |
| 15 | temporal/state | STATE-Bench v0.8.0 | MIT asserted, **no LICENSE blob pinned** | yes, `e2c8d7af…` / v0.8.0 | 450 claimed | upstream | n/a |
| 16 | temporal/state | Memora / FAMA | Apache-2.0, LICENSE blob pinned | yes, `a6493188…` | 71 subquestions | upstream | n/a |
| 17 | temporal/state | STALE | MIT code / CC-BY-4.0 data (lock assertion) | yes, `617c51dc…` | see §5 | upstream | n/a |
| 18 | temporal/state | MemSyco | MIT, LICENSE blob pinned | yes, `c31e2c85…` | 5 task files | upstream | n/a |

### 2B — evidence, power, cost

| # | instrument | ever run? | last result | ψ observed | MDE @80% | cost/run |
|---|---|---|---|---:|---|---|
| 1 | LongMemEval-S retrieval | **yes** | hit@5 baseline .614 → cap-1200 .843 (b=0, c=38, n=166) | 0.2289 | **10.7pt** — need n≥390 | $0 |
| 2 | LME-S 80q pool | **yes**, 21 arms | median arm b=4/c=8 | 0.1667 | **13.5pt** — need n≥285 | $0 |
| 3 | Phase-2 paid reader QA | **NO** — `paid_calls_executed: 0`, `authorization: null` | none | **unmeasured** | 7.3–9.2pt under proxy ψ | §7 |
| 4 | LongMemEval-V2 | **NO** — 9,405 harness lines, `official_output_files: 0` | none | unmeasured | — | §7 |
| 5 | Track R original | **yes**, 4 paired arms | fused vs scoped-BM25 @10: b=15, c=3 | 0.1000 | **6.7pt** (adequate) | $0 |
| 6 | Track R paraphrase | **NO** — zero scored arms | none | unmeasured | — | $0 to run |
| 7 | `coding_events_golden` | **yes** | BM25 vs MemPhant @10 Δ = −0.05 | ≥0.05 (bound) | **UNPOWERABLE at n=40** | $0 |
| 8 | GitHub lane | **NO** — fetch/extract/leakage/secrets scripts only, no runner | none | unmeasured | — | $0 to run |
| 9 | SWE-ContextBench | ran, **unpairable** | 3/4 no-memory baselines resolve; max gain 1 < required 2 | n/a | cannot express the effect | sunk |
| 10 | SWE-Explore | **NO** | none | unmeasured | — | blocked |
| 11 | Syndai docs gate | **yes** | hit@10 Δ = −0.133; QA Δ = −0.167 (MemPhant **loses**) | ≥0.133 (bound) | **13.1pt** — need n≥230 | $0 |
| 12–18 | see §5 | | | | | |

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
| 0.139 | best observed (80q pool median) | 0.764 | 7.28pt | 240 |
| 0.150 | **the packet's assumption** | 0.728 | 7.55pt | 260 |
| 0.229 | observed on this very lane (rung-7) | 0.542 | 9.24pt | 390 |

The preregistered claim of ~80% power is wrong at every ψ we have. The gap is not the
arithmetic — it is that **ψ for the reader endpoint has never been measured**. Retrieval
discordance is the only proxy we own, and the two candidates differ by 65%.

**Reachability.** The scored pool is 221 now, 259 sealed, ≈480 maximum. n=390 is therefore
reachable *only by spending the sealed confirmation set inside the screening run*, which
destroys the confirmation. That is a design decision, not a budget line.

### 3.2 Every other lane

| lane | n | ψ | MDE @80% | verdict |
|---|---:|---:|---|---|
| Track R fused vs BM25 @10 | 180 | 0.1000 | 6.73pt | **ADEQUATE** for 7pt — the only lane that is |
| Track R rank-order @10 | 180 | 0.1222 | 7.49pt | inadequate; need n≥210 |
| Track R render-loss @5 | 180 | 0.1944 | 9.51pt | inadequate; need n≥330 |
| Syndai docs hit@10 | 60 | ≥0.1333 | 13.08pt | inadequate; need n≥230 |
| Syndai docs QA | 60 | ≥0.1667 | 14.68pt | inadequate; need n≥285 |
| `coding_events_golden` @10 | 40 | ≥0.05 | — | **UNPOWERABLE** |
| LME-S non-regression | 166 | 0.0000 | — | **UNPOWERABLE** (b=c=0 by construction) |

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
