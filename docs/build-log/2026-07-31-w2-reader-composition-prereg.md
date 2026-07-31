# W2.1 — Chain-of-Note + structured composition: preregistration

Date: 2026-07-31
Status: **PREREGISTERED, NOT AUTHORIZED, NOT RUN.** No paid call has been made.
Plan: `docs/superpowers/plans/2026-07-30-substrate-and-accuracy-program.md` §W2.
Written and committed **before** any arm executes, per the standing rule that
analysis code and design are fixed before unblinding.

## Why this is the highest-value paid run available

The chat lane is **reader/composition-bound, not retrieval-bound**: R@10 sits at
0.83–0.94 while QA sits at **0.56**. Three independent lines of evidence say the
gap is in composition:

1. **The benchmark's own authors measured the fix.** LongMemEval §5.5, under
   *oracle retrieval* — our exact regime — reports **up to +10 absolute points**
   from Chain-of-Note plus a structured context format, consistent across model
   sizes, at **zero extra LLM calls** (it is a single-call output-format change).
   Their GPT-4o LongMemEval-S baseline is **0.606**; our 0.56 sits just under it,
   i.e. we are at parity with an un-augmented reader and the composition gain is
   simply unclaimed.
2. **OpenAI's ARC-AGI-3 result** (2026-07-29): retaining reasoning and compacting
   instead of truncating took GPT-5.6 Sol **13.3% → 38.3%** RHAE with the model
   unchanged and ~6× fewer output tokens. Cited here as evidence that context
   handling is load-bearing — **never as a cross-model comparison**, since it is
   self-run on their own harness.
3. Our own two coding-lane results this week: both wins came from what happens to
   evidence *after* it is found, not from finding more.

## Arms

Paired, same lattice, same corpus, same retrieval, differing only in reader
composition:

| arm | composition |
|---|---|
| **A (control)** | current prompt (`--prompt-version 3`, stratum-routed) |
| **B** | Chain-of-Note: per-item relevance notes before synthesis |
| **C** | structured JSON context format |
| **D** | B + C combined |

Singles before the combination — a combined arm whose parts cannot be attributed
is not promotable. If B and C are individually null, D does not run.

## Corpus — and two corrections to this document (2026-08-01)

Run on the **frozen 178-question development split**, not the sealed 259.

**Correction 1: the deprecation premise was false, and I asserted it as fact.**
This document blocked W2.1 on "LongMemEval's upstream deprecates the split we
currently use". Both upstream pages were re-fetched: **there is no deprecation
notice and no successor designation.** The only "deprecated" strings anywhere are
our own local filename and one sentence in our own repo. That premise is
withdrawn.

It also would not have mattered. W0.3 measured both splits, retrieval-only, $0:
**R@k does not move** — 0.6170 cleaned vs 0.6277 current at both k=5 and k=10,
one discordant question of 94 scored, exact two-sided McNemar **p = 1.0**,
bootstrap 95% CI [-0.0319, 0]. The cleaning is de-padding, not a content change:
it drops 1,243 haystack sessions of which **1,230 are empty**, leaves all 23,854
retained sessions byte-identical, and removes 0.07% of corpus turns. So our 0.56
is **not** an artifact of the split.

**Correction 2: the design below is underpowered, and this is the real blocker.**
The `d_min = 7pt` endpoint claims "~80% power at ψ≈0.15". Recomputed under the
exact test this packet names, and independently reproduced:

| n | ψ | power at 7pt |
|---:|---:|---:|
| 221 | 0.15 (assumed) | **0.728** |
| 221 | **0.229 (observed on this lane)** | **0.541** |
| 260 | 0.229 | 0.618 |
| 300 | 0.229 | 0.686 |
| 390 | 0.229 | 0.804 |

At the discordance rate this lane actually exhibits, the run is a **coin flip**.
True MDE is 7.6–9.2pt. Reaching 7pt needs n≈390, and 390 is only reachable by
consuming the sealed-259 confirmation set *inside* the screen — which would
destroy the one held-out asset the lane has.

**W2.1 does not launch on this design.** The $142.32 ceiling was sound; the
statistics were not. Before any spend, one of: (a) re-target the endpoint to the
effect the instrument can actually resolve (~9pt, which the Chain-of-Note
literature's ~+10pt claim would still clear), (b) find additional non-sealed
exposure to reach n≈390, or (c) accept a lower confidence level and preregister
it explicitly. Silently running at 54% power is the failure this whole program
exists to prevent.

## Endpoints and analysis — RE-TARGETED 2026-08-01, fixed before any run

**Primary endpoint: paired exact McNemar on answer correctness, arm vs control,
`d_min = 9pt`.** Re-targeted from 7pt to the effect this instrument can actually
resolve. Power recomputed on n=221 at the discordance rate **observed on this
lane** (ψ = 0.229), and independently reproduced:

| d_min | power at n=221, ψ=0.229 |
|---:|---:|
| 7pt (old) | 0.541 |
| 8pt | 0.666 |
| **9pt (adopted)** | **0.776** |
| 9.5pt | 0.823 |
| 10pt | 0.864 |

**Stated honestly: 9pt gives 77.6%, not 80%.** It is below the conventional
threshold and is adopted anyway, deliberately — 9.5pt would reach 0.823, but the
extra half-point of resolution is not worth narrowing what we can detect when the
literature effect we are chasing is "up to +10pt" (which sits at 0.864 here). We
are not going to round 0.776 up to "~80%"; that rounding is the exact failure this
program is built to prevent, and the evidence-contract checker would reject the
claim.

**Pre-commitments, binding:**

- **|Δ| < 9pt is recorded as NO FLIP**, not as a trend, a signal, or a direction.
- **ψ is an assumption, not a constant.** The run reports its *realized* ψ. If
  realized ψ > 0.25, power at 9pt falls below 0.72 and **the result is reported as
  underpowered regardless of its p-value** — a significant result at unplanned-low
  power is not promotable. Sensitivity, computed: ψ=0.18 → 0.873, ψ=0.229 → 0.776,
  ψ=0.28 → 0.684.
- **The sealed-259 is not touched.** Reaching 7pt would have needed n≈390, which
  is only available by consuming the confirmation set inside the screen. That
  trade is refused: the lane's one held-out asset is worth more than 1.5 points of
  resolution.
- Analysis code committed before unblinding.

**Secondary — abstention**, reader-judged (`abstain=true ∧ answer=null`). A net
abstention regression **blocks promotion regardless of the primary result** — the
fail-closed guardrail installed when the free proxy was rescinded in Phase 0.

**Two-sided naming:** Misapplication Rate and Appropriate Application Rate
reported separately, so a suppression win cannot masquerade as an application win.

**Cost/latency:** reader-token delta recorded beside the accuracy endpoint, and
the $0 SLO harness re-run before any default flip. Chain-of-Note lengthens output;
**"zero extra calls" is not "zero extra tokens"**, and that distinction is
preregistered rather than discovered afterwards. Note the harness must record
`loadavg`/`cpu_count` — a 6.5× apparent SLO regression on this host turned out to
be contention.

**Evidence contract:** the result artifact must carry a valid `evidence_contract`
block (`scripts/check_evidence_contract.py`) — computed MDE from the run's own
cells, harness settings, corpus snapshot, provenance class. It will fail closed
otherwise, and that is intended.

## Models — recorded lattice choice

Reader and judge from the committed lattice
(`benchmarks/manifests/reader_lattices.v1.json`), pinned to canonical snapshots.
Per the standing model-representativeness rule, **any promotion-capable result
requires a `claude-opus-5` robustness arm** — the model Syndai's Claude Code
executor actually serves — as a direction-agreement check, not a significance
test. A composition win that only reproduces on the eval-lattice reader is
fragile evidence, and single-reader results are the standard critique of vendor
evals.

## Budget

~$0.02–0.03 per call observed on this lattice. Four arms × 178 questions ×
(reader + judge) ≈ 1,424 calls ≈ **$30–45 realistic**, plus a conditional
robustness arm. A hard ceiling is derived mechanically at authorization time by
the same method as `scripts/derive_phase2_packet.py` — measured prompt bytes,
one-byte-per-token, provider maxima — never estimated.

## Kill gates

- ~~W0.3 has not reported → do not launch.~~ **SATISFIED 2026-07-31**: W0.3 reported p=1.0, no movement, and the deprecation premise is withdrawn. The remaining blocker is power, not the corpus.
- B and C both null at d_min → the composition thesis is wrong for our lane;
  record it and stop. Do not escalate to bigger prompts or more arms.
- Net abstention regression → no promotion, whatever the primary says.

## What this run may not claim

Not a SOTA claim; that language stays banned until a protocol run. Not
comparable to any vendor's self-run LongMemEval number — the published field
spans 60–95% on reader choice alone and **no neutral leaderboard exists**. This
measures *our* composition change against *our* control on one frozen split.
