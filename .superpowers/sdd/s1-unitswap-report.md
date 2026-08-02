# S1 — similarity unit swap: body → directive sentence

Branch `s1-unitswap` @ `ca60e4c4`, off `main` @ `0e874da0`. MemoryCode, 257
instances / 1,063 probes. **$0** — `paid_model_calls: 0` on every path.
Full log: `docs/build-log/2026-08-01-similarity-unit-swap.md`.

## Brief

B1's supersession extractor decided whether to retire a prior rule by
content-word Jaccard between two whole session bodies. A MemoryCode session is
~2,300 characters of small talk around one directive, so that Jaccard is
dominated by filler every session shares. Swap the similarity UNIT to the
best-matching directive sentence — deterministic, gold-independent, no model
call — and measure. τ re-calibration was explicitly out of scope (S1b).

## Method

One function, `structured_similarity(left, right, unit)`, behind
`--structured-unit {body,sentence}` defaulting to `body` so no existing arm
moved. The sentence unit takes max Jaccard over the two bodies' directive
sentences with quoted literals blanked. Gold-independent by construction: only
`body` text is reachable — no `topic`, `type`, `declarations` or probe.

The primitives were **moved** out of `measure_key_recovery.py` into the adapter
and imported back, not copied, so the offline census and the live extractor
cannot drift onto two stoplists. Verified behaviour-preserving: `recovery.json`
reproduces byte-identically ex-lineage, and `ledger-rescored.json` reproduces
every cell through the same function the live arm now calls.

**τ_sentence = 0.42 was RATE-MATCHED, not tuned** — the threshold reproducing
arm S's measured 0.13828 firing rate on S's own banked ledger, using no gold
field. Preregistered and committed at `2cec397a` before any arm launched.

Four arms, one tree / one binary pair / one corpus / one probe bank, all deltas
within-run: N (no-op isolator), S (body, on-tree replication), U (sentence),
R3 (rate-matched random ablation).

## Liveness — checked before any score was read

| arm | supersede edges | superseded | open txn | key overlaps | remainders recalled |
|---|---:|---:|---:|---:|---:|
| N | **0** (its definition) | 0 | 0 | 0 | 0 |
| S | 2182 | 1091 | 0 | 0 | 0 |
| **U** | **2042** | **1021** | 0 | 0 | 0 |
| R3 | 2196 | 1098 | 0 | 0 | 0 |

**U is live, not inert.** Compilation verified from the DB on the bench
superuser credential. Lineage identical across all four (`ca60e4c4`, server
`ba11520e0404…`, worker `39f24332c553…`, corpus `1edb12380ea3…`).

**U fires FEWER edges than either comparator (1,021 vs 1,091 / 1,098) and gains
more on every endpoint — precision, not aggression.**

## Cells

| arm | LSW | misapplication | hit@k |
|---|---:|---:|---:|
| N no-op | 0.314205 | 0.673565 | 0.842897 |
| S body | 0.362183 | 0.620884 | 0.807150 |
| **U sentence** | **0.405456** | **0.572907** | 0.827846 |
| R3 random | 0.333020 | 0.646284 | 0.825024 |

**Latest-state-wins**, cluster bootstrap over instances, 10k resamples, MDE
computed from each cell's own ψ:

| comparison | slice | ΔLSW | CI95 | n_d | perm p | MDE |
|---|---|---:|---|---:|---:|---:|
| **U − S** | confirmatory | **+0.0395** | **[+0.0129, +0.0669]** | 112 | 5.2e-03 | 0.0375 |
| **U − S** | full | +0.0433 | [+0.0197, +0.0677] | 154 | 5.0e-04 | 0.0334 |
| **U − R3** | confirmatory | **+0.0642** | **[+0.0319, +0.0982]** | 164 | 1.0e-04 | 0.0453 |
| U − N | confirmatory | +0.0901 | [+0.0604, +0.1216] | 135 | 1.0e-04 | 0.0411 |
| S − R3 | confirmatory | +0.0247 | **[−0.0033, +0.0532]** | 146 | 9.5e-02 | 0.0427 |
| S − N | confirmatory | +0.0506 | [+0.0269, +0.0753] | 117 | 2.0e-04 | 0.0383 |

Every n_d ≥ 79 — far above the structural floor of 6. No cell here is a
"NOT A MEASUREMENT".

**Retrieval (`hit@k`), same paired machinery:**

| comparison | slice | Δhit@k | CI95 | verdict |
|---|---|---:|---|---|
| S − N | full | −0.0357 | [−0.0532, −0.0175] | tax ESTABLISHED |
| U − N | full | −0.0151 | [−0.0350, +0.0047] | tax NOT established |
| U − S | full | +0.0207 | [0.0000, +0.0409] | **NEGATIVE — bound is exactly zero** |

## Verdict

**POSITIVE. The unit swap pays**: U − S = +0.0395 [+0.0129, +0.0669]
confirmatory, effect above the computed MDE on both slices, misapplication
moving with it (−0.0469 [−0.0756, −0.0190]).

**The more valuable result:** B1's semantic increment over a random rate-matched
policy was +0.0247 with a CI containing zero — a NEGATIVE by its own rule. This
run **reproduces that cell exactly** (+0.0247, [−0.0033, +0.0532]) on a
different tree and binary pair, then clears it with the sentence unit:
**U − R3 = +0.0642 [+0.0319, +0.0982]**. *Semantic target selection buys
something over retirement-by-rate* is now demonstrated where it was not. B1
§10's `as implemented` qualifier was correct: the negative was a fact about one
Jaccard at one τ, not about semantic selection.

**The offline prediction landed, and is reported because §2 committed to
reporting it either way.** Predicted live extractor precision: body 0.341,
sentence 0.765. Realized: **0.3410** at 1,091 fired and **0.7640** at 1,021
fired — three decimal places, on both arms, from a census that never ran a
server. Base rate 0.1488.

**Honest cost side:** S's retrieval tax is real and established; U's is not
distinguishable from zero; but **U − S on hit@k did not clear the bar either**,
so "the swap halves the retrieval tax" is a point estimate this experiment did
not demonstrate. Recorded as a NEGATIVE, not rounded up.

## Bounds

**MemoryCode's gold is recency-identified and wrong retirement is nearly free
(16 of 309 edges cost a gold, 5.2%), so this corpus COMPRESSES the effect.** A
policy that reads nothing earns +0.0292. Therefore **U's +0.0642 over R3 is a
LOWER BOUND, not an estimate** — on an instrument where naming the wrong prior
is almost costless, a selector that names the right one has little room to show
it. The sibling as-of lane has since shown this cannot be repaired by re-cutting
the same corpus: a corpus whose gold is computable from its own statements is
saturated by a short rule. The missing instrument is one where retiring the
wrong rule is EXPENSIVE, and it does not exist in this program.

**Transferable:** the relative ordering — sentence > body > random at matched
firing cost, with the semantic increment clearing zero for the first time. The
absolute magnitudes belong to MemoryCode.

Ceiling ratios (S closes 15.6% of headroom, U closes 29.6%) are **cross-lineage
context, not evidence** — the 0.622766 oracle was measured at `d6a39fb0`.

## Recommendation

**S1b (τ re-calibration) SHOULD proceed, against the SENTENCE distribution.**
τ = 0.42 was chosen to reproduce a firing rate, not to perform. Every arm's
ledger now banks `body_jaccard` AND `sentence_jaccard` for all 7,890 candidate
pairs, so the sweep is free, offline, and needs no ingest. Two constraints:
report the realized firing count beside every candidate τ (the distribution is
lumpy — plateaus are real), and treat `neither_returned` as a gating endpoint
(U already carries the highest of the four arms at 0.015992).

**Do not buy a model-based extractor on this.** The remaining gap is dominated
by session segmentation, not target selection.
