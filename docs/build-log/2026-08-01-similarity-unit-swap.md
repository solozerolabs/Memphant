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

> Sections 4 onward are the measurement. They were written after the arms ran
> and they change nothing in §1–3.8 above.

## 4. Mechanism liveness — checked before any accuracy number was read

`artifacts/2026-08-01-similarity-unit-swap/liveness-gate.json`. The gate ran
first and gates everything below it; had the treatment arm shown zero supersede
edges, "inert" would have been the whole report.

| arm | unit | τ | supersede edges | superseded units | open txn | key overlaps | remainders recalled |
|---|---|---:|---:|---:|---:|---:|---:|
| N no-op | — | 2.0 | **0** | 0 | 0 | 0 | 0 |
| S body | body | 0.25 | 2182 | 1091 | 0 | 0 | 0 |
| **U sentence** | sentence | 0.42 | **2042** | **1021** | 0 | 0 | 0 |
| R3 random | — | 0.25 | 2196 | 1098 | 0 | 0 | 0 |

**U is live, not inert.** N is exactly the isolator it claims to be: zero edges
of any kind, which is its definition rather than a result.

`paid_model_calls: 0` on all four. Corpus compilation verified from the DB on
the bench superuser credential, zero pending and zero failed jobs on every arm.

**One tree, one binary set, one corpus, one bank** — asserted by the gate, which
refuses to pass if any of them differ:

```
git_head    ca60e4c4  (git_dirty: true — see §9.1)
server      ba11520e0404…   worker  39f24332c553…
corpus      1edb12380ea3…   probes 1063 / instances 257 on all four arms
```

### 4.1 The realized firing rate — the preregistered caveat, resolved

§3.2 fixed τ_sentence = 0.42 by rate-matching on arm S's banked ledger and said
the match would not hold exactly live. It held better than promised:

| arm | candidates seen | fired | realized rate |
|---|---:|---:|---:|
| S body | 7,890 | 1,091 | 0.13828 |
| **U sentence** | 7,890 | **1,021** | **0.12940** |
| R3 random | — | 1,098 | 0.13924 |

**U fires FEWER edges than either comparator and gains more on every endpoint.**
It is not winning by retiring more aggressively. It is retiring more
accurately — which is the mechanism the swap predicted, stated here rather than
left to be inferred.

## 5. The offline prediction, checked against the live arms

§2 carried a precision prediction from the offline census and refused to convert
it into an LSW number. It can now be checked directly: score each arm's *live*
fired pairs against the gold co-declaration partition.

| | predicted (offline, at 1,091 firings) | **realized (live)** |
|---|---:|---:|
| body unit | 0.341 | **0.3410** at 1,091 fired |
| sentence unit | 0.765 | **0.7640** at 1,021 fired |

**The offline census predicted live extractor precision to three decimal
places**, on both arms, having never run a server. Base rate for a policy that
understands nothing is 1,174 / 7,890 = 0.1488.

Stated because §2 committed to stating it either way: **the prediction landed.**
It was still right to refuse to project an LSW number from it — precision is not
latest-state-wins, and §6 is where that question is actually answered.

## 6. The primary result

Full bank 1,063 probes / 257 instances; confirmatory 810 / 203. Cluster
bootstrap over instances, 10,000 resamples, seed 20260801. Every MDE **computed**
from that cell's own realized ψ by `instrument_power.min_detectable_effect`.

| arm | LSW | misapplication | hit@k |
|---|---:|---:|---:|
| N no-op isolator | 0.314205 | 0.673565 | 0.842897 |
| S body, τ 0.25 | 0.362183 | 0.620884 | 0.807150 |
| **U sentence, τ 0.42** | **0.405456** | **0.572907** | 0.827846 |
| R3 random, rate-matched | 0.333020 | 0.646284 | 0.825024 |

**Latest-state-wins.** Decisional slice named per §3.6.

| comparison | slice | ΔLSW | cluster CI95 | b/c | n_d | perm p | ψ | computed MDE |
|---|---|---:|---|---:|---:|---:|---:|---:|
| **U − S** | full | **+0.0433** | **[+0.0197, +0.0677]** | 100/54 | 154 | 5.0e-04 | 0.1449 | 0.0334 |
| **U − S** | **confirmatory** | **+0.0395** | **[+0.0129, +0.0669]** | 72/40 | 112 | 5.2e-03 | 0.1383 | 0.0375 |
| **U − R3** | full | **+0.0724** | **[+0.0435, +0.1027]** | 146/69 | 215 | 1.0e-04 | 0.2023 | 0.0394 |
| **U − R3** | confirmatory | **+0.0642** | **[+0.0319, +0.0982]** | 108/56 | 164 | 1.0e-04 | 0.2025 | 0.0453 |
| U − N | full | +0.0913 | [+0.0637, +0.1195] | 136/39 | 175 | 1.0e-04 | 0.1646 | 0.0356 |
| U − N | confirmatory | +0.0901 | [+0.0604, +0.1216] | 104/31 | 135 | 1.0e-04 | 0.1667 | 0.0411 |
| S − R3 | full | +0.0292 | [+0.0047, +0.0534] | 110/79 | 189 | 2.3e-02 | 0.1778 | 0.0370 |
| S − R3 | confirmatory | +0.0247 | **[−0.0033, +0.0532]** | 83/63 | 146 | 9.5e-02 | 0.1802 | 0.0427 |
| S − N | full | +0.0480 | [+0.0262, +0.0693] | 102/51 | 153 | 1.0e-04 | 0.1439 | 0.0333 |
| S − N | confirmatory | +0.0506 | [+0.0269, +0.0753] | 79/38 | 117 | 2.0e-04 | 0.1444 | 0.0383 |

**Every n_d is ≥ 79, far above the structural floor of 6.** No cell in this log
is a "NOT A MEASUREMENT".

**POSITIVE on the preregistered rule, on both slices.** U − S excludes zero on
the confirmatory slice (the decisional one for any comparison involving S) and
on the full bank, with an effect above the computed MDE in both.

### 6.1 The finding that matters most: the semantic increment is now demonstrated

B1 could not show that its extractor's *semantics* bought anything over a policy
that reads no bodies at all. Its confirmatory S − R3 was **+0.0247, CI
[−0.0033, +0.0532]** — an interval containing zero, which its own preregistration
defines as a NEGATIVE.

**This run reproduces that number exactly — +0.0247, CI [−0.0033, +0.0532], on a
different tree and a different binary pair** — and then clears it with the
sentence unit: **U − R3 = +0.0642, CI [+0.0319, +0.0982]** confirmatory,
**+0.0724 [+0.0435, +0.1027]** full.

That bit-for-bit reproduction is worth more than the headline. It says the
difference between the two lanes is the unit and not the weather.

**So the negative in B1 §10 was a fact about one Jaccard at one τ, exactly as
that section's `as implemented` qualifier insisted — and not a fact about
semantic target selection.** The qualifier earned its keep.

## 7. The retrieval endpoint — reported because it could have gone the other way

Arm K raised LSW while `hit@k` FELL, so a key change can pay on the primary and
charge for it in retrieval. Same paired machinery as the primary.

| comparison | slice | Δhit@k | cluster CI95 | b/c | n_d | perm p |
|---|---|---:|---|---:|---:|---:|
| **S − N** | full | **−0.0357** | **[−0.0532, −0.0175]** | 50/88 | 138 | 1.0e-04 |
| **S − N** | confirmatory | **−0.0420** | **[−0.0636, −0.0208]** | 35/69 | 104 | 2.0e-04 |
| **U − N** | full | **−0.0151** | **[−0.0350, +0.0047]** | 59/75 | 134 | 1.6e-01 |
| **U − N** | confirmatory | −0.0210 | [−0.0430, +0.0012] | 41/58 | 99 | 7.6e-02 |
| U − S | full | +0.0207 | [**0.0000**, +0.0409] | 62/40 | 102 | 6.2e-02 |
| U − S | confirmatory | +0.0210 | [−0.0012, +0.0429] | 48/31 | 79 | 9.7e-02 |

**Arm S pays a retrieval tax and it is established: −0.0357, CI clear of zero on
both slices.** This is the same direction Arm K found, and — recorded because it
is a correction to a banked log — **B1 never reported it**, because `hit_at_k`
carried no interval there. Re-analysing B1's own banked arms through the paired
machinery added here reproduces it: −0.0358, CI [−0.0532, −0.0175], n_d 138.

**U's retrieval tax is NOT established: −0.0151, CI [−0.0350, +0.0047], which
includes zero.** The honest reading is *the tax is not distinguishable from zero
for the sentence unit at this n*, not *there is no tax*. The point estimate is
negative and about 40% the size of S's.

**U − S on hit@k is a NEGATIVE by the preregistered rule.** The full-bank
interval's lower bound is exactly **0.0000** and the confirmatory interval
contains zero. U recovers roughly half of S's retrieval loss on the point
estimate, and **this experiment did not demonstrate that recovery at the bar.**
Stated as a negative rather than rounded up to a win.

**Misapplication moves with the primary and is unambiguous**: U − N −0.1007
[−0.1288, −0.0732], U − S −0.0480 [−0.0734, −0.0235], U − R3 −0.0734
[−0.1037, −0.0441]; all clear of zero on both slices.

## 8. How much of the headroom, and against what

**Cross-lineage, therefore context and not evidence.** The oracle ceiling
0.622766 was measured at `d6a39fb0`; this lane ran at `ca60e4c4`. Under the
shelf-life rule an absolute rate not differenced within a single run may not be
treated as a cell of this experiment.

Against headroom N → P of 0.308561: S closes **15.6%**, U closes **29.6%**.
The unit swap roughly doubles the fraction of the oracle gap that
gold-independent supersession recovers. **Re-cut the ceiling on this tree before
leaning on either ratio.**

## 9. Limitations

1. **`git_dirty: true` on all four arms, and what it is.** The stamp is taken
   when each report is written. One tracked file differed from `ca60e4c4`
   throughout: an 8-line COMMENT block in `scripts/with_scratch_db.sh`, since
   discarded in favour of trunk's bytes at `6fdcaf9d`. No executable line
   differed, and all four arms ran from one commit and one binary pair — which
   is the claim the lineage rule exists to protect. Recorded rather than
   explained away.
2. **τ_sentence was rate-matched, not optimised.** 0.42 is the best available
   match to S's firing rate on a lumpy distribution, not the best-performing
   threshold. **Re-calibrating τ on the sentence distribution is untouched
   headroom and is S1b.** Nothing here estimates its size.
3. **One unit per session**, inherited whole. Still the largest known cause of
   the remaining gap; the swap does not address it.
4. **One target, top-ranked only.** Unchanged and out of scope.
5. **U's `neither_returned` is the highest of the four** (0.015992 vs N's
   0.006585). Small, and consistent with a more aggressive-per-edge policy
   removing rows, but it is the cost line to watch if the unit is tuned harder.
6. **This is not a measurement of the LLM structured-state subsystem.**
   `MEMPHANT_STRUCTURED_STATE` stayed dark.

### 9.1 THE BOUND, restated where the verdict is read

§3.8 stated it before the measurement and it is repeated here because a bound
placed only ahead of a number gets dropped in the retelling just as reliably as
one placed only behind it.

**MemoryCode's gold is recency-identified, and wrong retirement is nearly free —
16 of 309 edges cost a gold, 5.2%. This corpus COMPRESSES the effect this lane
measures.** A random policy that reads nothing earns +0.0292 here.

Consequences for how §6 may be read:

- **U's +0.0642 over R3 is a LOWER BOUND on what the unit swap is worth**, not an
  estimate of it. On an instrument where naming the wrong prior costs almost
  nothing, a selector that names the right one has little room to show it.
- The sibling as-of lane has since shown this cannot be fixed by re-cutting the
  same corpus: **a corpus whose gold is computable from its own statements is
  always saturated by a short rule.** The missing instrument is one where
  retiring the wrong rule is EXPENSIVE, and it does not exist in this program.
- **Nothing here transfers to a corpus where a retired convention is later
  re-asserted**, or where currency is signalled by something other than recency.

The transferable claim is the **relative ordering** — sentence beats body beats
random, at matched firing cost, with the semantic increment clearing zero for
the first time. The absolute magnitudes belong to MemoryCode.

## 10. Verdict

**Did the unit swap pay? YES, at the preregistered bar, on both slices, subject
to §9.1's compression bound stated ahead of it.**

`ΔLSW U − S = +0.0395, CI [+0.0129, +0.0669]` confirmatory (`+0.0433
[+0.0197, +0.0677]` full), n_d 112, computed MDE 0.0375. Misapplication moves
with it, −0.0469 [−0.0756, −0.0190].

**The more valuable result is §6.1.** B1's semantic increment over a random
rate-matched policy was an underpowered positive whose interval contained zero,
and this run reproduces that cell exactly before clearing it: **U − R3 = +0.0642,
CI [+0.0319, +0.0982]**. *Semantic target selection buys something over
retirement-by-rate* is now demonstrated, where it previously was not.

**The cost side is honest and it is mixed.** Arm S's retrieval tax is real and
established (−0.0357, clear of zero). U's is not distinguishable from zero
(−0.0151, CI [−0.0350, +0.0047]) — but **U − S on hit@k did not clear the bar
either**, so "the swap halves the retrieval tax" is a point estimate this
experiment did not demonstrate, and it is recorded as a NEGATIVE.

**U fires 1,021 edges against S's 1,091 and R3's 1,098 — fewer edges, more gain.
The improvement is precision, not aggression**, which is exactly what Arm K
predicted the next lever would have to be.

**Should τ be re-calibrated now (S1b)? YES — and against the SENTENCE
distribution, not the body one.** The reasoning is now measured rather than
argued: τ = 0.42 was chosen to reproduce a firing rate, and it lands at live
precision 0.7640 against a base rate of 0.1488. The banked ledger for every arm
now carries `body_jaccard` AND `sentence_jaccard` on all 7,890 candidate pairs,
so the re-calibration is free, offline, and needs no new ingest. Two things S1b
must respect:

1. **Precision at 1,021 firings is 0.764 and at 2,000 it was 0.556 offline** —
   the distribution is lumpy and the plateau structure is real, so a τ sweep
   must report the realized firing count beside every candidate τ, not just the
   threshold.
2. **`neither_returned` is the endpoint to watch** (§9.5). U already carries the
   highest of the four arms; a τ tuned for LSW alone can push rules out of the
   pool entirely, which is the failure Arm K's `hit@k` drop was.

**Do NOT buy a model-based extractor on the strength of this.** The remaining
gap is dominated by session segmentation (one unit per session), not by target
selection, and the $0 lever that just paid has a second free move left in it.

## 11. Reproduce

```bash
cd /Users/sidsharma/Memphant-s1-unitswap        # branch s1-unitswap @ ca60e4c4
docker start memphant-postgres-1
cargo build --release --bin memphant-server --bin memphant-worker --bin memphant-cli
#   server ba11520e0404…   worker 39f24332c553…
PY=<venv-with-pyarrow>/bin/python PY=$PY bash scripts/run_s1_unitswap.sh
python3 scripts/s1_liveness_gate.py --dir $OUT --out $OUT/liveness-gate.json   # BEFORE any score
bash scripts/run_s1_analysis.sh
```

Launch detached (`setsid`/`nohup caffeinate`); a full four-arm run is ~3.5h
wall on a shared box. `pyarrow` is the only extra dependency.

## 12. Artifacts

| artifact | contents | lineage |
|---|---|:--:|
| `arm-u-sentence.json` | **Arm U**, sentence unit τ 0.42, 1,063 rows, 7,890-pair dual-unit ledger | ✅ |
| `arm-s-body.json` | **Arm S**, body unit τ 0.25, on-tree replication | ✅ |
| `arm-n-noop.json` | **Arm N**, no-op isolator, zero edges by construction | ✅ |
| `arm-r3-random.json` | **Arm R3**, rate-matched random ablation | ✅ |
| `liveness-gate.json` | the gate, run before any score was read | ✅ |
| `analysis/{u-vs-s,u-vs-r3,u-vs-n,s-vs-r3,s-vs-n}.{full,confirmatory}.json` | 10 paired analyses, each with its own computed `evidence_contract` | ✅ |
