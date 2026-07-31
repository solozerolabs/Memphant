# Null review — auditing this program's own "no effect" conclusions (2026-07-31)

Cost: **$0 paid API spend.** No model call, no benchmark run, no DB. Every number
below is recomputed from rows already on disk by
`scripts/audit_null_review.py`; the machine-readable output is
`docs/build-log/artifacts/null-review-ledger.json`.

No checkbox, default, cutover, deployment, or SOTA claim moves in this document.

## The governing fact

> **The two-sided exact McNemar test has no rejection region below n_d = 6.**
> At n_d = 5 the most extreme split has p = 2 × 0.5⁵ = 0.0625 > 0.05, so nothing
> rejects. A "p = 1.0, no difference" from n_d = 3 is not a null. It is a
> non-measurement, and it may be hiding a win we abandoned.

## Headline

**27 conclusions recorded as null / flat / parity / p = 1.0 were audited. One is
an unqualified valid null.**

| classification | count | meaning |
|---|---:|---|
| **NON-MEASUREMENT** | **12** | n_d < 6, or no recoverable rows. Never had power at any effect size. |
| **UNDERPOWERED** | **9** | n_d ≥ 6, but the true MDE exceeds the 7pt the program preregisters. |
| **FALSE NULL** | **3** | re-derivation rejects at α = 0.05. |
| **VALID NULL (ψ-fragile)** | **2** | adequate at the point estimate of ψ only; fails at ψ's upper 95% bound. |
| **VALID NULL** | **1** | adequately powered. |

Plus 2 **controls** (results never recorded as nulls, used to prove the method
reproduces known rejections) and 1 entry excluded as a **duplicate measurement**
(see §5). 19 of the 27 were re-derived from per-question rows; 4 rest on
committed 2×2 cells with no rows behind them and are marked as such; 4 could not
be checked at all (§6).

So: **44% of the nulls in this window were never measurements, and only 1 in 27
is a null this program is entitled to state without qualification.** That is the
honest finding and it is not softened anywhere below.

---

## 1. FALSE NULLs, by name

All three sit on the coding lane, all three share one mechanism, and all three
were produced by the **same instrument defect**: the original Track R golden bank
leaks lexically at **3.93×** (target coverage 0.3960 against a 0.1008 floor). The
gold text contains the query's own identifiers, so any BM25-family scorer wins by
string match and a dense channel has nothing left to contribute. On the
paraphrase bank — same corpus, identifiers withheld, concentration 2.018× — the
verdicts invert.

### FN-1 · "Dense embeddings did not work on this lane" — k=5

`2026-07-30-coding-lane-first-win.md:60`, from
`2026-07-30-phase1r-retrieval-bm25.md:116` ("only up to a null vs BM25", p = 0.200).

On the paraphrase bank, `overlap_dense` vs `overlap_off`, fused hit@5:
**both 14 / dense-only 38 / control-only 1 / neither 127**, n_d = 39,
δ = **+21.7pt**, exact p < 1e-9.

### FN-2 · The same conclusion at k=10

Same source. Fused hit@10: **both 37 / dense-only 46 / control-only 3 /
neither 94**, n_d = 49, δ = **+23.9pt**, exact p < 1e-10. This is the anchor case
the review was calibrated against; it is confirmed from rows, not from prose.

### FN-3 · "Hybrid fusion is therefore not recommended on this lane" — NEW as a live defect

`2026-07-30-phase1r-retrieval-bm25.md:126`, from −10/+3 (p = 0.092) and −3/+2
(p = 1.000) on the contaminated bank.

On the paraphrase bank, `bm25code_dense` vs `bm25code_off`, fused hit@10:
**both 84 / hybrid-only 29 / lexical-only 5 / neither 62**, n_d = 34,
δ = **+13.3pt**, exact p = **3.9e-05**.

The statistic itself is not new — `2026-07-31-w0-2-paraphrase-arms.md:91,157`
already reports +29/−5 at p = 3.9e-05. **What is new is that the superseded
conclusion is still standing.** `2026-07-30-phase1r-retrieval-bm25.md:126` still
reads "Hybrid fusion is therefore not recommended on this lane", and
`2026-07-30-coding-lane-first-win.md:60` still reads "Dense embeddings did not
work on this lane. … The best configuration uses **no embeddings at all**", with
no correction pointer in either file. The measurement was corrected; the
recommendation was not. **Neither of these two documents may be cited on dense or
hybrid fusion until they carry a pointer to W0.2.**

**Caveat carried, not buried.** W0.2's own 2026-07-31 correction records that the
five arms were built from `af-w0-instrument`, which contains neither `f67f2b2a`
(render-loss) nor `3fc4eede` (exact-channel), and requires a re-run on trunk
before the numbers are cited again. That correction also states the **fused**
figures are unaffected because retrieval is upstream of both fixes. Every entry
above uses fused hits, never packed — the packed lineage is the one the
correction withdraws. FN-1/2/3 therefore survive the correction as stated, and
the trunk re-run (§4, rank 1) is still required to close them.

---

## 2. The Memora failure mode is systemic, not a one-off

The named anchor — Memora's "flat 43/71 vs 44/71" — is **register confirmed**
from the shipped `.fama.json` rows: matched by `evaluation_question_id`, the cells
are **both 31 / replay-only 12 / pilot-only 13 / neither 15**, n_d = **25** of 71,
ψ = 0.352. Marginals one cell apart; a third of the graded cells moved. Carry the
nesting: the 71 subquestions sit inside **15 parent questions** (mean 4.7), so
exact McNemar at n = 71 is **anticonservative** and the 20.3pt MDE is a **floor**
on the true MDE.

The same shape recurs four more times in this window, each time with near-identical
marginals hiding large discordance:

| result | marginals | n_d | ψ | what was recorded |
|---|---|---:|---:|---|
| `phase1r-armC-at10` — dense vs scoped BM25 | 0.9000 vs 0.8944 | **31** | 0.172 | "16/15, p = 1.000 — null" |
| `w02-dense-vs-bm25code-para-at10` | 0.4611 vs 0.4944 | **50** | 0.278 | (never stated as a verdict) |
| `r1-d2-parity` — D2 vs Syndai | 0.2500 vs 0.2417 | **23** | 0.192 | "Parity at 7× volume" |
| `memora-flat-4371` | 43/71 vs 44/71 | **25** | 0.352 | "raw accuracy stayed flat" |

**A marginal comparison is not a paired test.** In every row above the two arms
disagree on a sixth to a third of the bank and net out to roughly zero. That is
evidence of two *different* systems whose errors are complementary — which is an
argument for fusion — and it was read as "the same system twice".

---

## 3. The audit table

`both / after-only / before-only / neither`. `after` is the arm named first in
the comparison. ψ, MDE and power are computed by `scripts/instrument_power.py`
against the two-sided exact test at α = 0.05, at each result's **own realized ψ**
— never an assumed one. `rows` = re-derived from per-question vectors;
`cells` = classified from a committed 2×2 with no rows behind it, **not**
re-derived.

| # | id | source | 2×2 | n | n_d | ψ | MDE@80 | p | class | basis |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|
| 1 | `w02-dense-paraphrase-at10` | w0-2:263 | 37/46/3/94 | 180 | 49 | .272 | 11.2pt | <1e-10 | **FALSE NULL** | rows |
| 2 | `w02-dense-paraphrase-at5` | w0-2:263 | 14/38/1/127 | 180 | 39 | .217 | 10.0pt | <1e-9 | **FALSE NULL** | cells |
| 3 | `w02-hybrid-vs-bm25code-para-at10` | w0-2:91 | 84/29/5/62 | 180 | 34 | .189 | 9.4pt | 3.9e-05 | **FALSE NULL** | rows |
| 4 | `r15-r6-parity` | r15:16 | 21/18/8/73 | 120 | 26 | .217 | 12.3pt | 0.0755 | UNDERPOWERED | rows |
| 5 | `memora-flat-4371` | register:465 | 31/12/13/15 | 71 | 25 | .352 | ≥20.3pt | 1.0 | UNDERPOWERED | rows |
| 6 | `r1-d2-parity` | r1:34 | 18/12/11/79 | 120 | 23 | .192 | 11.5pt | 1.0 | UNDERPOWERED | rows |
| 7 | `w02-dense-vs-bm25code-para-at10` | w0-2 | 61/22/28/69 | 180 | 50 | .278 | 11.3pt | 0.480 | UNDERPOWERED | rows |
| 8 | `phase1r-armC-at5` | p1r:25 | 125/15/24/16 | 180 | 39 | .217 | 10.0pt | 0.200 | UNDERPOWERED | rows |
| 9 | `phase1r-armC-at10` | p1r:25 | 146/16/15/3 | 180 | 31 | .172 | 9.0pt | 1.0 | UNDERPOWERED | rows |
| 10 | `wave-sibling-qa` | wave | 52/4/7/37 | 100 | 11 | .110 | 9.3pt | 0.549 | UNDERPOWERED | rows |
| 11 | `wave-cleanup-neutral` | wave:20 | 54/6/5/35 | 100 | 11 | .110 | 9.3pt | 1.0 | UNDERPOWERED | rows |
| 12 | `r1-a4-chunks-null` | r1:36 | 15/2/4/99 | 120 | 6 | .050 | — | 0.688 | UNDERPOWERED¹ | rows |
| 13 | `phase1r-armB-at10` | p1r:23 | 158/5/3/14 | 180 | 8 | .044 | 4.4pt | 0.727 | VALID NULL | rows |
| 14 | `phase1r-densebm25code-at5` | p1r:122 | 161/3/10/6 | 180 | 13 | .072 | 5.7pt | 0.092 | VALID NULL (ψ-fragile) | rows |
| 15 | `r1-a1-breadcrumb-null` | r1:32 | 14/3/5/98 | 120 | 8 | .067 | 6.6pt | 0.727 | VALID NULL (ψ-fragile) | rows |
| 16 | `phase1r-densebm25code-at10` | p1r:122 | 170/2/3/5 | 180 | **5** | .028 | — | 1.0 | **NON-MEASUREMENT** | rows |
| 17 | `wave-session-quota-qa-null` | wave:28 | 57/1/2/40 | 100 | **3** | .030 | — | 1.0 | **NON-MEASUREMENT** | rows |
| 18 | `r0-small-vs-base-parity` | r0:32 | 54/2/1/43 | 100 | **3** | .030 | — | 1.0 | **NON-MEASUREMENT** | rows |
| 19 | `phase1r-lme-n30` | p1r:180 | 16/2/1/11 | 28 | **3** | .107 | — | 1.0 | **NON-MEASUREMENT** | cells |
| 20 | `abs-sentinel-packrendercap` | register §4.1 | 5/0/2/5 | 12 | **2** | .167 | — | 0.5 | **NON-MEASUREMENT** | rows |
| 21 | `w03-cleaned-split-at5` | lme-split:16 | 58/0/1/35 | 94 | **1** | .011 | — | 1.0 | **NON-MEASUREMENT** | rows |
| 22 | `packadj-exact-at5` | exact-chan:252 | 166/0/0/14 | 180 | **0** | 0 | — | 1.0 | **NON-MEASUREMENT** | cells |
| 23 | `packadj-exact-at10` | exact-chan:252 | 168/0/0/12 | 180 | **0** | 0 | — | 1.0 | **NON-MEASUREMENT** | cells |
| 24 | `w02-dense-vs-bm25code-para-at5` | w0-2 | — | — | — | — | — | — | **NON-MEASUREMENT** (unrecoverable) | — |
| 25 | `w02-hybrid-vs-bm25code-para-at5` | w0-2 | — | — | — | — | — | — | **NON-MEASUREMENT** (unrecoverable) | — |
| 26 | `syndai-docs-hit10` | syndai-gate | — | — | — | — | — | — | **NON-MEASUREMENT** (unrecoverable) | — |
| 27 | `coding-events-golden-bm25` | code_lane_controls | — | — | — | — | — | — | **NON-MEASUREMENT** (unrecoverable) | — |
| — | `w03-cleaned-split-at10` | lme-split:137 | 58/0/1/35 | 94 | 1 | .011 | — | 1.0 | *duplicate of #21* | rows |
| C1 | `forgeteval-lineage-complete` | register §4.4 | 133/111/0/15 | 259 | 111 | .429 | 11.7pt | <1e-30 | control (rejects) | rows |
| C2 | `syndai-docs-qa` | syndai-gate | 3/0/10/47 | 60 | 10 | .167 | 14.7pt | 0.0020 | control (rejects) | rows |

¹ `r1-a4-chunks-null` clears the n_d ≥ 6 floor by exactly one pair, but at n = 120
and ψ = 0.05 **no effect of any size reaches 80% power** — it is unpowerable
rather than merely underpowered.

### Three notes the table cannot carry

**#13/#14/#15 — "valid" is doing less work than it looks.** These clear the bar
only because their ψ is very low, and ψ is itself estimated from 8, 13 and 8
discordant pairs. Recomputing the MDE at the upper 95% bound of ψ pushes #14 and
#15 above 7pt, which is why they are marked ψ-fragile. #13 (`phase1r-armB-at10`)
is the one unqualified valid null in the window: it rules out |δ| ≥ 4.4pt, i.e.
plain BM25 does not beat the scoped lexical control at k=10 by 4.4 points or
more. That is a real statement and it does license the A′ tokenizer increment.

**#22/#23 are not really statistics and the source document knows it.**
`2026-07-30-exact-channel-magnitude.md:230` says so explicitly — the evidence is
`packed_context_identical: true`, byte-identity of the packed context, which is a
far stronger argument than any p-value. Classified NON-MEASUREMENT because the
p = 1.0 printed beside it is vacuous and must never be cited alone. The
conclusion stands; the statistic does not support it.

**#21 is the same shape.** The W0.3 conclusion — the LME-S cleaning does not move
R@k — rests on the corpus diff (23,854 of 23,854 retained sessions byte-identical,
−0.07% of turns), not on the McNemar. The corpus diff is dispositive. But
`2026-07-31-w2-reader-composition-prereg.md:166` cites the *p-value* to release a
W2.1 blocker: "W0.3 reported p=1.0, no movement". That citation is invalid as
written and should be re-pointed at the corpus diff.

---

## 4. What to re-run, ranked by cost if the null is false

**This ranking is the product of the review; the table above is its evidence.**
Ordered by the size of the win we may have abandoned — the upper 95% bound on δ
that the data still permits, weighted by whether anything is currently built on
the conclusion — *not* by how cheap the re-run is.

**1 · Re-run the four W0.2 paraphrase arms on trunk, and propagate the retraction.**
Ceiling: **+26.5pt** (FN-2), and it is not a hypothetical — it is already measured.
Currently load-bearing in two uncorrected documents. $0, warm cache. This is
W0.2's own required action #1 and it is still open. Until it lands, the coding
lane's shipped recommendation ("no embeddings at all") contradicts the only
leak-free measurement we own. Add the two k=5 arm-vs-arm contrasts (#24, #25),
which are unrecoverable today because the paraphrase artifact banks
`per_question` at k=10 only.

**2 · R1.5 R6 — the docs-lane unlock.** Ceiling **+15.5pt**; observed δ **+8.3pt**,
exact p = **0.0755**, MDE 12.3pt. `2026-07-12-r15-rank-compression.md:16` refused
the unlock on a bootstrap CI whose floor touched exactly 0.000, and the exact
paired test was never run on those rows. It agrees the effect is not proven — but
the instrument could not have proven an 8pt effect at this n, so "proof is not"
was a foregone conclusion. This is the largest *undecided* deferral in the window
and the one where a re-run changes a product decision.

**And it is expensive to settle.** At the observed ψ = 0.217, resolving 7pt needs
**n = 370** pooled — the corpus is two 60-question sets, so that is roughly six
mined sets, not a re-run of the two we have. Resolving the *observed* 8.3pt is no
cheaper. The honest options are (a) mine to n ≈ 370 and decide it properly, or
(b) state plainly that R6 is undecidable at the corpus size we are willing to
build and stop citing the 2026-07-12 refusal as if it were evidence of no effect.
Do not run a third set and call the result an answer: n = 180 still has an MDE
near 10pt.

**3 · Dense vs BM25 on the clean bank at k=5, and the fusion question generally.**
Ceiling +4.9pt on the recoverable @10 leg, but the *shape* is the point: 50
discordant cells of 180 (#7) and 31 (#9). The two channels are complementary on
a sixth to a third of the bank. Neither "dense loses" nor "dense adds nothing"
is supported; what the data actually says is that nobody has measured the fusion
that keeps both. Falls out of item 1 at no extra cost.

**4 · Memora / FAMA.** Ceiling +13.2pt, but the MDE is ≥20.3pt and the effective
n is nearer 15 than 71 because of the nesting. **Do not re-run this instrument to
resolve this question** — it cannot resolve it. Re-run only with more parent
personas, or drop the lane's claim to a qualitative one. Recorded here so the
decision is explicit rather than deferred again.

**5 · R1 D2, "parity at 7× volume".** Ceiling +8.9pt, 23 discordant cells,
MDE 11.5pt. The budget policy treats volume as free on the strength of it. Cheap
to fold into item 2 — same corpus, same reader lattice.

**6 · `sibling_gather` (#10) and `session_quota` (#17).** Ceilings +4.2pt and
+2.4pt. `session_quota`'s null is a non-measurement (n_d = 3) and the mechanism
is still default-off because of it. `sibling_gather` is UNDERPOWERED, and a
deletion is pending against it — but that deletion **also** rests on an
independent band-emptiness argument which does hold, so the deletion is safe on
other grounds. Neither is worth a dedicated run; both should be re-derived if
those levers are ever revisited.

**7 · R0 embedder default (#18).** n_d = 3 — a program-wide default set by a
non-measurement. The ceiling is genuinely small (+2.95pt) because discordance is
tiny, so the exposure is low even though the conclusion is unsupported. Flagged,
not scheduled.

**Explicitly not worth re-running:** #22, #23 (identity arguments, statistic
irrelevant), #21 (corpus diff is dispositive), #16, #19 (superseded in their own
documents by powered arms), #20 (already rescinded in Phase 0).

---

## 5. Two traps checked, not assumed

**The @10 relabelling.** On the LME-S slice the maximum `first_answer_rank` ever
observed is 5, so `hit@10` is identical to `hit@5` and every chat-lane "k=10"
figure is the k=5 figure relabelled. Verified at runtime rather than taken from
the register: max rank is **2** on both W0.3 arms and on the rung-7 baseline, and
**5** on the rung-7 cap-1200 arm — all ≤ 5. Consequence: the W0.3 result is
reported as two nulls (`lme-cleaned-split.md:16` and `:137`) and is **one
measurement printed twice**. It is excluded from the counts as a duplicate.
The code lane is *not* affected: Track R hits genuinely differ between k=5 and
k=10, so those pairs are independent and are counted separately.

**ψ recorded as a lower bound.** Four lanes published only a delta or a bootstrap
CI and never committed `b` and `c` (governance item Z6). Two are unrecoverable
(#26, #27). **But one is not, and the register is wrong about it**: the register
records Syndai docs QA as ψ ≥ 0.1667 with `b` and `c` "never committed
separately". The per-question rows survive in
`syndai-gate/reader-{memphant,syndai}.json` — 60/60 ids join — and the exact cells
are **both 3 / MemPhant-only 0 / Syndai-only 10 / neither 47**. True ψ = 0.1667
exactly, so the register's bound was tight and its MDE of 14.67pt is right; only
its provenance claim is wrong. The retrieval leg (#26) really is unrecoverable:
no per-question retrieval vector was ever banked.

---

## 6. What could not be checked, and why

**Four conclusions are permanently unrecoverable** (all classified
NON-MEASUREMENT-by-unrecoverability, and named so the process failure is on record):

1. **`syndai-docs-hit10`** — `gate_compare.json` commits only a bootstrap CI on
   the retrieval delta. No per-question retrieval vector exists. ψ is bounded
   below at 0.1333 forever. Half of the C2 docs-slice drop rationale.
2. **`coding-events-golden-bm25`** — only `paired_delta_recall_at_10 = −0.05` was
   published. n = 40 in one repo, 4-question held-out slice. The register's
   "unpowerable at n=40, no conclusion at any k is defensible" is confirmed and
   sharpened: it is also unrecoverable.
3. **`w02-dense-vs-bm25code-para-at5`** and 4. **`w02-hybrid-vs-bm25code-para-at5`**
   — the paraphrase artifact's `per_question` rows carry only `*_hit_at_10`, and
   its committed k=5 cells are against the baseline arm only, never arm-vs-arm.
   Recoverable by re-running (item 1), unlike 1 and 2.

**Four more are classified from committed 2×2 cells with no rows behind them**
(#19, #22, #23, and the k=5 leg of FN-1). Their cells are trustworthy — they are
committed alongside the arms that produced them — but this review did not
re-derive them and does not claim to. For #19 the underlying per-arm reports
under `track-r/phase1r/` are gitignored.

**Three conclusions were deliberately not audited** because they are not paired
tests and no 2×2 exists or could exist: Phase 1r §4's "Lever A is measured inert"
(a mechanism argument — the prefilter covers a median 0.985 of unit bodies and
the residual 4 misses are policy drops, identical in all five arms), Phase 1r §6's
"what did not move: packing" (a bucket-count comparison across five arms with no
paired vector), and `2026-07-31-preference-writepath.md`'s "what it did not move"
(a write-path identity claim, and the document says so at :162 — "identity, not a
null result").

### A process failure that is real, and one that is not

**Real: 87 of 88 canonical evidence paths are untracked.**
`canonical-artifact-allowlist.txt`'s header states these files were "force-added
out of an otherwise ignore-only tree". `git ls-files` says otherwise: **1 of 88
is tracked**, in either repo. The other 87 — including every Memora, MemSyco and
state-memory artifact, 14,679 files — exist as ignored files in one directory on
one machine. This review read the Memora rows from
`/Users/sidsharma/Memphant/docs/build-log/artifacts/unified-sota-2026071[34]/`
and could not have read them anywhere else. **If that directory is lost, the
temporal/state lane's entire evidence base is lost with it.** Either force-add
them as the header claims, or amend the header to say they are not backed up.

**Not real: the "missing" track-r artifacts.** `track-r/`, `track-r-paraphrase/`
and `lme-cleaned-split/` are absent from main and present only in the
`accuracy-first` worktree. That is a **branch gap, not a loss** — they are
tracked, and main acquires them on merge. An audit run from main alone would
report the coding lane's last two days as "never run", which would be false.
The ledger records, per artifact, which tree answered the read and whether git
tracks it, so the two cases can never be conflated again.

### `check_evidence_contract.py`

Run: `evidence_contract_ok contracted=4 pending_retrofit=45`. What it actually
checked: the four `2026-07-31-preference-writepath/analysis-*.json` artifacts
against the full schema plus the power, leakage, bar, mechanism, corpus and
instrument guards, and the CI-workflow lint. The `contracted` list is **no longer
empty** — it grew from 0 to 4 at `f268afbd` — but 45 of 49 promotion-capable
artifacts remain pending retrofit, and **none of the 27 conclusions audited here
is among the 4**. The checker's `_guard_power` implements exactly the n_d ≥ 6
floor this review applies; had it been in force across the window, at minimum the
12 non-measurements would have failed closed at bank time.

The ratchet then fired on this review's own ledger, correctly — a new JSON under
`docs/build-log/artifacts/` carrying a `delta` key at depth ≤ 4. It is now
registered as **contracted with `decisional: false`**. The contract models one
result and this ledger indexes thirty, so every field the single `power` block
cannot represent is the literal `"unverified"`, which is precisely the encoding
the schema prescribes for that case. **This ledger may never carry a promotion or
kill decision.** It audits the artifacts that do. `contracted` is now 5.

---

## 7. Recommendations

1. **Propagate the W0.2 retraction** into
   `2026-07-30-phase1r-retrieval-bm25.md:126` and
   `2026-07-30-coding-lane-first-win.md:60`, then re-run the four arms on trunk.
   Neither document may be cited on dense or hybrid fusion until it does.
2. **Re-point** `2026-07-31-w2-reader-composition-prereg.md:166` from W0.3's
   p-value to W0.3's corpus diff.
3. **Ban the bare null.** No document may record "null", "flat", "parity" or
   "no effect" without the four cells and n_d beside it. `_guard_power` already
   enforces this for contracted artifacts; extend the retrofit to the paired
   analyzers first, not last.
4. **Amend or honour** the `canonical-artifact-allowlist.txt` header (§6).
5. **Correct the register's provenance claim** for Syndai docs QA (§5): the cells
   are recoverable, and its ψ is exactly 0.1667 rather than a lower bound.
6. **Bank `per_question` at every k that is reported.** The paraphrase artifact
   reports k=5 verdicts from rows it only banked at k=10.

## Reproduce

```sh
cd /Users/sidsharma/Memphant-accuracy-first     # branch af-null-review
python3 scripts/audit_null_review.py            # prints the table, $0, no DB
python3 scripts/audit_null_review.py --write    # rewrites the ledger artifact
python3 scripts/check_evidence_contract.py
```

`scripts/audit_null_review.py` resolves each artifact against this worktree first
and `/Users/sidsharma/Memphant` second, and records which tree answered.
