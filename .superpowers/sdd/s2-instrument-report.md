# S2 — a discriminating instrument. Report.

**Branch `s2-instrument` · 2026-08-01 · $0 · no paid model call on any path ·
not merged to main.**

Survey: `docs/build-log/2026-08-01-discriminating-instrument-survey.md`.

---

## The trap, first

**A corpus whose gold is not recency-identified will make BOTH arms score
worse.** The +3.01pp on MemoryCode is measured where both arms already encode
the correct rule, i.e. near a ceiling by construction. A level drop is the
expected outcome and is not evidence about supersession.

**Whoever runs any instrument in this report must preregister, before the first
arm executes:**

1. a **DROP in absolute level in both arms** is expected;
2. the endpoint is the **GAP BETWEEN ARMS**, `Δ = treat − control`, and nothing
   else;
3. the **direction of the prediction** — the hypothesis is that **Δ GROWS**
   relative to +0.0301, because the trivial control loses its free ride. If Δ
   shrinks or crosses zero, plan §5's exit price is back on the table;
4. **both levels and Δ in one table**, with the MemoryCode levels beside them.

This is in the survey as §0, not as a footnote.

**A second trap, new from this survey:** a corpus that defeats *recency* may be
defeated by a *different* trivial rule. On TempLAMA, measured on the shipped
files, `max(timestamp)` is correct on **55.5%** of rows but
`most_frequent_answer` is correct on **70.7%**. Every adopted instrument must
preregister its **full** trivial baseline set — `max(observed_at)`, mode,
first-declared, and `max(observed_at ≤ t)` — not just A-recency.

---

## What was established

**1. MemoryCode's gold-is-recency premise, re-verified here rather than cited.**
`scripts/external_instrument_adapter.py:163-179` — `current_index, current_topic =
occurrences[-1]`, distractors `occurrences[:-1]`, `observed_at(index) = EPOCH +
index minutes` (`:81`).

**2. MemoryCode contains ZERO re-assertion.** Measured on the pinned parquet:
1,063 probe-bearing groups, 4,679 declarations, and **0 keys where a value
reappears after being displaced**. Regime (a) is structurally absent and no
re-cut can produce it.

**3. MemoryCode CAN be re-cut to break recency identification — at $0.**
**3,608** as-of probes over **257** instances exist in the corpus we already
hold, under a licence already adjudicated SAFE (**[F] Apache-2.0**, real LICENSE
read). This is the cheapest discriminating instrument available and it is
recommended first.

**4. The machinery it needs is already in the tree**, verified by reading it:
`RecallRequest.valid_at` → `valid_for_query` (`memphant-core/src/lib.rs:10163-10167`)
is a live interval filter; `correction_rectangles` (`:1064-1167`) with
`start_lt(None, Some(now)) == true` (`:1221-1227`) **does** mint a historical
remainder, so history is preserved; `gate_run_memphant.py` already threads
`valid_at`.

**5. Exactly two licence-clean sources supply all four regimes, and both must be
built rather than adopted:** **MDN `browser-compat-data`** (**[F] CC0-1.0**,
fetched here — coding domain, 704 re-assertion browser-pairs across 325
features, 7,454 bounded-validity statements, and **no timestamp axis at all**,
so `max(observed_at)` is not a weak baseline but an undefined one) and
**Wikidata** (**[F] CC0-1.0**, fetched here — re-assertion and deprecated-rank
currency confirmed with QIDs, ≥959,026 bounded P39 statements).

**6. No ready-made public benchmark supplies what we need.** The closest
adoptable artifact is **VersiCode** (**[F] Apache-2.0**, verified here; its HF
card says `mit` — a genuine contradiction). Of ten memory-agent benchmarks
examined, **none contains a coding task**, and MemoryAgentBench's "conflict
resolution" competency is `argmax(serial_number)` **with the sort key printed in
the prompt**.

---

## Ranked recommendation

| rank | instrument | regimes | licence | cost | decision it unlocks |
|---|---|---|---|---|---|
| **1 — run now** | **MemoryCode as-of re-cut** (build; survey §4) | (c) (d) · **not (a): 0 re-assertions measured** | **[F] Apache-2.0**, already held and pinned | **$0**, ~1 day + ~3 h compute | Does the bitemporal machinery beat `max(observed_at ≤ t)` — a 20-line read rule — once the corpus stops handing both arms the answer? |
| **2 — the coding answer, build** | **MDN `browser-compat-data`** (survey §4b) | **(a) (b) (c) (d), in the coding domain** | **[F] CC0-1.0**, fetched here; **the GitHub API object agrees**; no attribution burden | **$0** in money, ~1–2 weeks build | Closes the empty coding-cross-session-memory slot with an **externally-authored, CC0 gold**. Wrong retirement is **executable** — needless polyfill vs runtime throw |
| **3 — build** | **Wikidata direct extraction** (survey §3.1) | **(a) (b) (c) (d)**, non-coding | **[F] CC0-1.0**, no attribution obligation | **$0**, ~1 week | The only *non-synthetic* source with all four regimes in one corpus, including deprecated-rank currency |
| **4 — adopt** | **VersiCode** | (a) (b) + **coding** | **[F] Apache-2.0** repo; **HF card says `mit` — contradiction, trust the repo**; StackOverflow SA carry-over on redistribution | $0 to measure | The only *ready-made* public artifact combining re-assertion, non-recency currency and code |
| **5 — adopt** | **RoundEdit** (unit-level probe) | (a) + (d) | **[F] MIT, ZJUNLP 2023** | $0 | Cleanest licence carrying **both** (a) and (d); prices collateral forgetting directly |
| **6 — measure locally only** | **ChroKnowBench** | (a) (b) + a matched unaffected-neighbourhood control | **[C] only; LICENSE ABSENT** | $0 | Highest measured re-assertion density found (**23%**); latest-wins correct on only **48.7%** |

**Instruments to design against but not depend on:** **AToKe** — its `HES`/`HRS`
score answers to explicitly historical prompts, which is the exact shape of our
defect, and its licence is **ABSENT** (404 on both mirrors). Copy the metric,
not the data.

---

## Power — computed with `scripts/instrument_power.py`, not asserted

Required n for the program's preregistered 7pt decision (`D_MIN = 0.07`),
two-sided exact McNemar at 80%:

| ψ | 0.10 | 0.15 | 0.20 | 0.25 | 0.30 | 0.40 | 0.50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **n** | 166 | 260 | 340 | 425 | 505 | 665 | 825 |

**Discrimination is cheap in n and expensive in level:** moving from ψ = 0.10 to
ψ = 0.40 — which is what losing recency identification does — costs only ~4× in n.

MDE at 80% for the as-of re-cut, n = 3,608, at several assumed design effects
(probes cluster in 257 instances at mean 14.04 each, so DEFF is binding and is
**unmeasured until the run**):

| effective n | ψ=0.20 | ψ=0.30 | ψ=0.40 |
|---:|---:|---:|---:|
| 3,608 (DEFF 1 — do not use) | 2.11pp | 2.58pp | 2.98pp |
| 1,804 (DEFF 2) | 3.00pp | 3.66pp | 4.22pp |
| 902 (DEFF 4) | 4.26pp | 5.20pp | 5.99pp |
| 451 (DEFF 8) | 6.06pp | 7.38pp | 8.51pp |

**The 7pt decision survives to DEFF ≈ 8 even at ψ = 0.30.**

---

## Cost to have an instrument that can falsify our positive results

| | |
|---|---|
| **Rung 1 — MemoryCode as-of re-cut** | **$0.** ~60 lines of adapter, ~5 lines of harness, one third arm implemented in the harness, ~3 h of compute on the existing scratch-DB rig. Deterministic regex grading: **no reader, no judge, no paid call.** |
| **Rung 2 — MDN BCD coding instrument** | **$0 in money.** One 19 MB CC0 JSON, no clone, no scrape. ~1 day arc extraction + filter, ~3–4 days session synthesis and probe generation, ~2 days executable scoring in Node. **0 paid calls to reach a retrieval decision**; an agent/reader endpoint is a separate authorization and must be gated on the retrieval result. |
| **Rung 3 — Wikidata bitemporal bank** | **$0 in money.** ~1 week of build. WDQS returns 5,000 tuples in 0.84 s; a 200k-tuple corpus is under an hour of query time inside the documented 60 s/query and 60 s-CPU-per-minute limits. Needs a prose renderer (triples → text units) and a probe generator. |
| **Rung 4 — VersiCode / RoundEdit adoption** | **$0** to measure. Licence-clean for internal measurement; VersiCode needs a StackOverflow-provenance check before any redistribution. |
| **Anything paid** | **Not requested.** No candidate in this report requires a paid run to reach a decision. The paid frontier (SWE-ContextBench `Related` at ~$545) is a *public-claim* purchase, not a discrimination purchase, and is unchanged by this work. |

**So: the price of an instrument that can falsify our positive results is zero
dollars and about a week of engineering.** The binding constraint has never been
money.

---

## What must NOT be inferred from this report

- Nothing here says the +3.01pp is wrong. It says it is **measured in a regime
  that cannot distinguish** the bitemporal rule from `max(observed_at)`.
- No default, checkbox, cutover or SOTA claim moves.
- No instrument here is a *coding cross-session memory* benchmark. That slot
  remains **ABSENT** and this survey confirms it from a second direction: of ten
  memory-agent benchmarks examined, **none contains a coding task**, and
  MemoryAgentBench's "conflict resolution" competency is `argmax(serial_number)`
  with the sort key printed in the prompt.
