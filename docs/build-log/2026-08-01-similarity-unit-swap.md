# S1 — swapping B1's similarity UNIT from session body to directive sentence

Branch `s1-unitswap`, off `main` @ `0e874da0`. MemoryCode, 257 instances /
1,063 probes. `$0` — no model call on any path, no network, no paid arm.

> **Sections 1–3 are the PREREGISTRATION. They were written and committed
> before any arm was launched, and nothing below §3 changes a word of them.**

---

## 1. The one-sentence question

B1's supersession extractor decides whether to retire a prior rule by
content-word Jaccard between **two whole session bodies**. Does comparing the
two bodies' **best-matching directive sentences** instead — same information,
filler removed, still deterministic and still gold-free — improve
latest-state-wins, and what does it cost retrieval?

## 2. Why the unit and not the threshold

A MemoryCode session is roughly 2,300 characters of mentor small talk wrapped
around one directive sentence. A whole-body Jaccard is therefore dominated by
filler every session shares: B1's live candidate distribution is median 0.194 /
p95 0.283 / **max 0.5213**, and the adapter's own default τ = 0.35 would have
fired 32 times in 7,890.

Two readings of that compressed distribution were available. "τ is
miscalibrated" was the original one, and it is the *worse* supported: the
key-production lane re-scored B1's own 7,890 banked candidate pairs holding the
pairs fixed and changing only the compared object, and precision at arm S's
actual operating point went **0.341 → 0.765**
(`docs/build-log/artifacts/2026-08-01-key-production/ledger-rescored.json`,
`decisional: false`). Re-calibrating τ first would optimise the diluted scale
and bake it in. **Unit first, τ second.** τ re-calibration is explicitly out of
scope here and is a separate lane (S1b).

The second motivation is independent and comes from the other side of the
program. **Arm K** showed that the cost of a lossy key is *retrieval*: K's
`hit@k` FELL 0.8429 → 0.7761 while its LSW rose, whereas the oracle-keyed arm P
has `hit@k` 0.9247 and RISING. The deficit is therefore **precision, not
coverage** — a lossy rule wrongly retires live rules and they leave the pool.
A unit swap attacks exactly that quantity.

**What the 0.341 → 0.765 figure is NOT.** It is a precision comparison at a
matched firing count, computed on pairs B1's ranker had **already returned as
top-1**. It says nothing about what a sentence-level scorer *retrieves*, and it
is not a projected LSW. **No LSW number is projected in this document.** The
swap is measured live or it is not measured.

## 3. The design

### 3.1 The change, in full

One function, `external_instrument_adapter.structured_similarity(left, right,
unit)`:

- `body` — whole-body content-word Jaccard. **B1's original unit, unchanged.**
- `sentence` — `max` Jaccard over every pair of directive sentences of the two
  bodies, quoted literals blanked.

A directive sentence is a sentence carrying a quoted literal — this corpus's
directive shape. Blanking the literal is deliberate and is the choice every
key-production rule already makes: two sessions stating the *same* convention
about *different* literals ("prefix with `q_`", later "prefix with `x_`") are
precisely the co-declaring pair supersession exists to catch, and leaving the
literal in would push them apart.

**Gold-independent by construction, and this is load-bearing.** Every input is
`body` text. `topic`, `type`, `declarations` and the probes are not reachable
from the scorer. An arm that read any of them would be a CEILING and would
carry `decisional: false`; this one does not.

**Deterministic and free.** Regex plus set arithmetic. `paid_model_calls: 0`.

**One definition, not two.** The primitives were *moved* out of
`measure_key_recovery.py` into the adapter and imported back under their
original names, rather than copied, so the offline census and the live
extractor cannot drift onto two different stoplists. Verified
behaviour-preserving: `measure_key_recovery.py` reproduces its banked
`recovery.json` byte-identically ex-lineage, and `rescore_structured_ledger.py`
— re-pointed at the shared function — reproduces every cell of its banked
`ledger-rescored.json` (`distribution`, `precision_at_matched_firing_count`,
`pairs_scored` 7,890, `co_declaring_pairs` 1,174, `b1_named` 1,091).

### 3.2 The threshold for the sentence unit, fixed here, chosen without gold

The two units live on **different scales**, so B1's τ = 0.25 is meaningless on
the sentence unit and running it there would confound the unit swap with a
firing-rate change. τ is therefore **rate-matched, not tuned**: the sentence
threshold is the one that fires at arm S's own measured operating point on arm
S's own banked ledger.

Computed on the 7,890 banked candidate pairs, using no gold field of any kind —
only the score distribution:

| τ (sentence) | pairs firing | rate |
|---:|---:|---:|
| 0.40 | 1,268 | 0.16071 |
| **0.42** | **1,082** | **0.13714** |
| 0.44 | 998 | 0.12649 |
| 0.50 | 978 | 0.12395 |

Arm S fires 1,091 / 7,890 = **0.13828**. **τ_sentence = 0.42** is the closest
attainable match (1,082, within 0.9% of S's rate); the distribution is lumpy —
Jaccard over short sentences — so nothing lands nearer. Any τ in (0.4167,
0.4286] gives the same 1,082 and 0.42 is the round number inside that plateau.

**This is ex-ante rate matching and it will not hold exactly.** Retirement
changes the live pool, which changes what recall returns, which changes the
candidate set. The realized firing rate is reported as measured, not assumed.

### 3.3 The arms — one tree, one set of binaries, one haystack, one stage

| arm | unit | τ | what it is |
|---|---|---:|---|
| **N** | — | 2.0 | **no-op isolator.** Supersession never fires. Unit-invariant by construction: with no target ever named, the two units produce byte-identical writes. |
| **S** | body | 0.25 | **B1's arm S, re-run on THIS tree.** The published S ran at `0ecf8cb2` on other binaries and cannot be differenced against anything here. |
| **U** | sentence | 0.42 | **the treatment.** |
| **R3** | random | 0.25 | rate-matched random ablation, fire-rate 0.13828 — the uninformative policy at matched cost. |

Every arm: same tree, same server/worker/cli sha256, same corpus sha256, same
probe bank, same stage. **All deltas are within-run.** A headline in this
program was voided once for comparing across pipeline stages; nothing here
crosses a tree.

### 3.4 Endpoints — both of them, and why

**Primary: `appropriate_application` (latest-state-wins).**

**Co-primary: `hit_at_k`.** Reported with the same paired, instance-clustered
statistics as the primary, not as a bare descriptive rate. Arm K moved LSW up
and `hit@k` down in one run; **a report that omits retrieval hides the cost of
a key change.** The analysis script was extended to analyse `hit_at_1` and
`hit_at_k` through the identical machinery (`retrieval_endpoints`);
`secondary_descriptive` is unchanged so older readers still resolve.

Also reported: `misapplication`, `neither_returned`.

### 3.5 The comparisons

- **U − S** — *the question this lane exists to answer.* The unit swap at
  matched firing rate. Decisional.
- **U − N** — does the sentence-unit arm beat no supersession at all.
- **U − R3** — the **semantic increment** over a policy that understands
  nothing. §10.1 of the B1 log names this as the quantity a unit swap must
  move: on the banked ledger the co-declaring base rate is 1,174 / 7,890 =
  0.149, which body clears by 0.192 and sentence by 0.616.
- **S − N**, **S − R3** — on-tree replications of B1's own cells.

### 3.6 Slices

Reported on **both**, with the decisional slice named per comparison:

- **Full bank** — 257 instances / 1,063 probes.
- **Confirmatory** — `sha256(group_id) % 4 != 0`, 203 instances / 810 probes.

B1's τ = 0.25 was calibrated on the dev slice (`% 4 == 0`), so any comparison
**involving arm S** is dev-contaminated on the full bank and its decisional
reading is the confirmatory slice. τ_sentence = 0.42 was fixed by a gold-free
rate match, so **U − N** and **U − R3** are uncontaminated on the full bank.
`U − S` is read on the confirmatory slice and reported on both.

### 3.7 Decision rule, fixed in advance

**POSITIVE** iff the cluster-bootstrap 95% CI on ΔLSW excludes zero in the
favourable direction, on the slice named decisional for that comparison.
A CI including zero is a **NEGATIVE**. The MDE is **computed** from the
realized ψ of this run's own cells via `instrument_power.min_detectable_effect`
— never asserted.

**`n_d >= 6` structural floor.** Fewer than six discordant pairs on an endpoint
is written as **"NOT A MEASUREMENT"** with the required n. Never "a tie", never
"no effect".

**Mechanism liveness is checked BEFORE any score is read**, and gates the whole
report: supersede-edge count **> 0**, `remainders_recalled == 0`, and corpus
compilation verified from the DB. **If the edge count is zero the arm is inert
and "inert" is the entire report.**

No guard, bar or threshold is weakened to obtain a number. UNVERIFIED, INERT
and BLOCKED are acceptable outcomes.

## 3.8 THE BOUND, STATED BEFORE THE NUMBER

**This corpus COMPRESSES the effect this lane is trying to measure, and that
bound governs how any result below may be read.** It is stated here, ahead of
the measurement, because in this program a bound placed after a CI has been
dropped in the retelling three times.

MemoryCode's gold is **recency-identified**: `load_memorycode` takes
`occurrences[-1]` and stamps `observed_at` in session order, so the correct
answer is always the most recent declarer and the distractors are always
*earlier* declarations of the same key. Two consequences:

1. **Retiring the old row is close to a restatement of the scoring rule.** A
   random policy that reads nothing earns +0.0259 here (B1 §8.3).
2. **Wrong retirement is nearly free.** From B1 §3.1's committed table at
   τ = 0.25: of 309 edges, **16 retire a gold (5.2%)** and 293 retire a
   distractor or nothing (94.8%).

(2) is the one that binds this lane. **A better target selector cannot
demonstrate much on an instrument where choosing the wrong target costs
almost nothing.** The metric distance between a careless selector and a careful
one is structurally small here *whatever either one understands*. So:

- A **positive** result is a lower bound on what the unit swap is worth, not an
  estimate of it.
- A **null or small** result **does not establish that the unit swap is
  worthless** — it is exactly what a compressed instrument produces from a real
  improvement, and "more probes" is the wrong response (B1 §9.9: powering this
  instrument harder buys a tighter interval around a compressed number).
- Nothing here transfers to a corpus where a retired convention is later
  **re-asserted**, or where currency is signalled by something other than
  recency. No such corpus exists in this program yet.

---
