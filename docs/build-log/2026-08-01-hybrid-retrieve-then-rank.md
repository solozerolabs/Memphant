# S8 — retrieve-then-rank: MemPhant narrows, the agent ranks. And what N should be.

**Date:** 2026-08-01 · **Branch:** `s8-hybrid` (base `main` @ `a43cd574`) ·
**Spend ceiling:** $180 · **Status at the time this section was written:
PREREGISTRATION. No cell of this lane has been seen.**

Two parts. **Part A is the preregistration and is committed before any paid arm
runs.** Part B is appended after the runs and never edits Part A. If they
disagree, Part A wins and the disagreement is the finding.

---

# PART A — PREREGISTRATION (committed before any cell was seen)

## A.0 The question, and why it is not "does the hybrid win"

S4 (`docs/build-log/2026-08-01-agentic-search-controls.md`) measured, on the
Track R paraphrase bank, n=180, same haystack, same stage, same query string:

| arm | hits@10 | rate | spend |
|---|---:|---:|---:|
| agentic `grep` (agent + `grep`/`read_event`/`list_events` over the attempt's raw events) | 174 | **96.67%** | $25.93 |
| MemPhant `bm25code_dense` (shipped default) | 106 | 58.89% | $0 at recall |
| scoped BM25 | 46 | 25.56% | — |

Paired: b=1, c=69, n_d=70, exact McNemar p=1.2e-19, Δ=−37.78pp.

**But the deficit is ordering, not finding.** From the banked fusion probe
(`~/.memphant-private/track-r-paraphrase/run-fusion/fusion_probe-provenance.json`),
recomputed here from its own `channel_table` rows:

| gold within MemPhant's top-N | 1 | 2 | 4 | 5 | 8 | 10 | 16 | 24 | 32 | 48 | 64 | 96 | 128 | any rank |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| questions (of 180) | 30 | 46 | 68 | 78 | 99 | **113** | 127 | 144 | 152 | 159 | **164** | 168 | 168 | **169** |
| rate | .167 | .256 | .378 | .433 | .550 | .628 | .706 | .800 | .844 | .883 | .911 | .933 | .933 | **.939** |

MemPhant *finds* the gold at **93.89%**, within 2.8pp of `grep`'s realized
96.67%, and then orders it out of the top ten on a third of the bank. The
agentic arm is not out-*searching* us; it is out-*ranking* us. So:

> **Arm H — the agent's visible universe is MemPhant's top-N fused pool, and
> nothing else. It ranks within that pool and returns ten.**

## A.1 The deliverable is an operating point, not a number

10 and 64 are constants inherited from a harness. Nobody derived them. The
quantity that matters is a product of two curves:

```
hit@10(N)  ≈  Coverage(N)  ×  RankAcc(N)
```

* **Coverage(N)** — P(gold within MemPhant's top-N). The table above. **Free**,
  known before any spend, and it is each N's **hard ceiling**.
* **RankAcc(N)** — P(the agent puts gold in its top ten | gold is somewhere in
  the N it was shown). **Nobody has measured this.** It should fall as N grows
  (attention dilution) while token cost rises.

Coverage rises, RankAcc falls ⇒ **there is an interior maximum. That `N*` is the
deliverable**, together with the accuracy-per-token curve, which may peak at a
different and smaller N.

**`hits@10 / Coverage(N) · 180` IS RankAcc(N)**, and it is the curve nobody has.
Every N is reported against **its own** ceiling. A sweep point is never called a
failure for missing a ceiling it never had access to.

## A.2 The ceiling, preregistered, and it is unflattering

**Arm H cannot exceed 169/180 = 0.9389 at any N**, because gold is absent from
MemPhant's pool entirely on 11 questions. `grep` realized 0.9667. **So H CANNOT
beat `grep` on this bank; its ceiling is already below `grep`'s realized score.**
Per N, the ceiling is the Coverage row above: N=16 ⇒ 127/180 = 0.7056, N=32 ⇒
152/180 = 0.8444, N=64 ⇒ 164/180 = 0.9111, whole pool ⇒ 169/180 = 0.9389.

Three questions, then, and all three are first-class outcomes:

1. How much of the **113 → 169** ranking headroom does agent ranking recover, and
   at which N?
2. At what token cost relative to `grep`'s measured **$0.144/question**?
3. Is the residual gap to `grep` entirely the 11 out-of-pool questions, or does
   the agent *also* fail to rank correctly inside a pool that contains the gold?

*"H reaches ~0.93 at a fraction of `grep`'s tokens"* is a strong product finding.
*"H reaches 0.70, so the agent cannot rank our pool either"* is a **more
important** one: it would mean the pool is both badly ordered and badly composed,
and it would kill the hybrid thesis. Both are reported the same way.

## A.3 Two bounds carried AHEAD of the numbers, not trailing them

**1. The bank overshot its own correction.** Track R paraphrase bans identifier
surfaces, and real engineers name files. Absolute q→target lexical coverage
brackets as `paraphrase 0.1346 < human 0.175–0.287 < original 0.396`. A `grep`
control is a lexical instrument and this is the lexically hardest point in that
bracket — harder than any human would pose. S4's rule carries over unchanged:
**any margin `grep` holds over a MemPhant-derived arm here is a LOWER bound.**

**2. Nothing here transfers to scale.** The haystack is ONE attempt: pool median
**122.5** units, max 199, and MemPhant's whole pool is already nearly the whole
attempt. That is what makes S4's `list_events` brute force affordable at ~4 tool
calls and ~$0.144/question. At repo scale, or across sessions, `list_events` has
no analogue and enumeration is infeasible. **Any `N*` derived here is `N*`
measured at a 122-item haystack.** It is not extrapolated past it; a sibling $0
lane is characterising the scale axis separately and is expected to meet this
one there.

## A.4 Arms — same stage, same endpoint, same query, nested haystacks

| | arm | mechanism | haystack |
|---|---|---|---|
| **H(N)** | **hybrid retrieve-then-rank** | `anthropic/claude-opus-5` with `list_pool` / `grep_pool` / `read_item` over MemPhant's first **N** fused candidates, returning ≤10 ranked | **MemPhant's top-N fused pool** |
| **F** | MemPhant fused@10, the stage-matched retriever comparator | the first 10 of the same fused pool, unranked by any model | the same pool |
| **T** | MemPhant shipped default, packed@10 | `bm25code_dense` recall + packing | the same pool |
| **C1** | agentic `grep` (banked from S4, not re-run) | agent + `grep`/`read_event`/`list_events`, no MemPhant | the attempt's **raw events** — a SUPERSET |

`H(N) ⊆ H(all) ⊆ pool ⊆ attempt events = C1's haystack`. The nesting is the
point: H is handed strictly less than C1 and must make up the difference by
judging better.

**Stage identity.** Every arm terminates in ten bodies through the same two
functions, `gate_common.evidence_row(golden, bodies, 10)` and
`gate_common.provenance_hit(golden, bodies, 10)`, and every report declares
`endpoint_contract == "gate_common.provenance_hit@10 over top-10 bodies"` —
byte-identical to S4's string. The analysis refuses to pair two reports that do
not both declare it. A headline in this program was voided for scoring one arm
after packing against another's plain ranked top-10; **F, not T, is H's
retriever comparator**, because H like F returns unpacked ranked bodies. T is
reported beside it and is never the paired headline.

**Query identity.** Every arm receives `code_lane_run_memphant.retrieval_query`,
banked per question in the pool dump.

**Budget symmetry.** H's caps are byte-identical to C1's: 12 tool calls, 16
turns, 24,000 completion tokens/question, `grep` ≤25 matches × 300 chars,
`read_item` ≤6,000 chars, `select` ≤10. Realized cost is reported beside T's and
F's zero.

## A.5 Mechanism liveness — pool containment, proved from the arm's own output

*If the agent can reach outside the pool, this is S4 re-run and the comparison is
void.* It is asserted, not assumed, three ways:

1. **Structural.** `PoolTools` holds N strings. No offered tool can name an
   event, a sequence, a file or a path; there is no `list_events`, no filesystem.
   The tool list is stamped into every report.
2. **Per call.** An out-of-range `read_item` returns an error string carrying no
   data, and the count of such requests is reported.
3. **Per returned body.** Every body the arm returns is matched against the exact
   set of bodies that question's agent was handed. **On any mismatch the runner
   refuses to write a score** (`pool_containment_violations`), and the only flag
   that disables it, `--allow-pool-escape`, exists so the guard has a name to
   refuse by and marks its own report as not comparable to S4.

`is_gold` is banked in the pool dump for the post-hoc decomposition and **never
enters the agent's context**; it is read after the episode ends.

## A.6 Design — staged, so the sweep is cheap and the answer is powered

**Stage 0 — pool dump. $0.** One shipped-default recall per golden on a scratch
DB, banking the fused pool with bodies. Configuration is byte-identical to the
banked fusion probe (`--embed-model small --mode fast --k 10 --budget-tokens
8192 --lexical-scorer bm25-code --limit-attempts 1`), so this run's own packed
top-10 is a **check** on the banked 106/180, not a new configuration. Every paid
stage below reads this one file.

**Stage 1 — stub round trip. $0.** The full arm contract against a deterministic
local tool-calling model: dispatch, argument validation, an out-of-range item,
truncation, budget ceiling, selection resolution, pool containment and scoring.
Three adapters in this program failed at first contact, two of them after money
was authorized.

**Stage 2 — coarse sweep, reduced n.** `N ∈ {4, 8, 16, 32, 64, 128, all}` on a
**preregistered fixed subset of 60 goldens**, committed as
`docs/build-log/artifacts/s8-hybrid/sweep-subset.json` —
`random.Random(20260801).sample(sorted(question_ids), 60)`, drawn and committed
**before any cell was seen**, never hand-picked. Purpose: locate the knee.

> **STAGE 2 IS EXPLICITLY NOT A MEASUREMENT.** At n=60 most contrasts will fall
> under the n_d ≥ 6 floor. Its p-values decide nothing and are reported only
> where n_d ≥ 6, with the required n stated wherever it is not. It selects the
> confirmation points; it does not answer the question.

**Stage 3 — confirmation at full n=180** on the best **2** values of N from the
sweep **plus N=64 as the pre-committed anchor**, so the coarse and fine stages
are tied together and the result stays comparable to S4's arms. The sweep run at
each confirmed N is resumed, not re-billed; an errored row is re-run, never
carried.

## A.7 Power — computed for this lane, never inherited

The instrument register's 6.73pt belongs to the ORIGINAL bank. S4 recomputed the
paraphrase bank's planning MDE at n=180 as **9.38pp** (ψ=0.189) and realized
13.34pp on its decisive contrast. The same planning value is adopted here and
**every contrast's realized ψ and MDE are recomputed from its own discordant
pairs** with `scripts/instrument_power.py` and reported beside it.

**Decision rule, fixed here.** Let Δ = rate(H) − rate(comparator), n_d =
discordant pairs, p = two-sided **exact** McNemar.

1. **n_d < 6 ⇒ "NOT A MEASUREMENT" plus the required n.** Never "a tie", never
   "no effect".
2. **H wins:** Δ ≥ +MDE and p < 0.05 and n_d ≥ 6.
3. **The comparator wins:** Δ ≤ −MDE and p < 0.05 and n_d ≥ 6.
4. **No detectable difference at this power:** n_d ≥ 6, neither of the above,
   reported with the 95% CI on Δ and the statement that effects inside the band
   are unmeasured, not absent.

**The product verdict** turns on H vs F (does agent ranking recover the ranking
loss?) and on cost against `grep`'s $0.144/question. H vs C1 is reported for
completeness and is capped by §A.2 — H **cannot** win it, and a loss there is
not evidence against the hybrid.

## A.8 Two decompositions, required at every N

* Of the misses at each N: how many are **out-of-view** (gold not in the N handed
  over — the retriever's failure, fixable only by better retrieval) versus
  **in-view-but-ranked-out** (the ranker's failure, fixable by prompting or a
  smaller N)? Both are computed from the pool dump's `is_gold`, post-hoc.
* Does RankAcc degrade **smoothly** with N, or fall off a cliff? A cliff means a
  hard limit on how many candidates an agent can usefully judge — a far more
  transferable finding than any single number, and the one thing here with a
  chance of surviving the §A.3 scale bound.

## A.9 Spend plan and the necessity test

| stage | what | ceiling |
|---|---|---:|
| 0 | pool dump (local server, local embedder) | $0 |
| 1 | stub round trip | $0 |
| 2 | coarse sweep, 7 values of N × 60 questions | ~$65 |
| 3 | confirmation, 3 values of N × up to 120 remaining questions | ~$45 |
| — | re-runs of errored rows and reserve | rest of $180 |

Necessity is satisfied by the brief: this is the only lane that can produce an
operating point, and an operating point is what a product ships.

Provider pinned `only: ["anthropic"]`, `allow_fallbacks: false`,
`max_price = {prompt: 5.0, completion: 25.0}` USD/M, verified against the live
OpenRouter catalogue before the first paid stage. An upstream price change fails
the call rather than silently costing more. The ledger reports **pinned price ×
reported tokens** — an upper bound, not settled cost — and banks generation ids
for reconciliation.

## A.10 Lineage

Every artifact carries git HEAD, branch, dirty flag, the sha256 of every input
(corpus, golden, pool dump), the model and provider pin, and — for the pool
dump — the sha256 of the served MemPhant binaries and the runner's own runtime
identity. An artifact without lineage did not happen.

---

# PART B — RESULTS (appended after the runs; Part A above is unedited)

*Not yet written. No paid cell has been seen at the time Part A was committed.*

---

# PART A2 — STAGE-3 SELECTION (committed after the sweep, before any n=180 cell)

The coarse sweep (n=60, `docs/build-log/artifacts/s8-hybrid/analysis-sweep.json`,
**NOT A MEASUREMENT** per §A.6) landed for $70.10:

| N | hits@10 /60 | ceiling = Coverage(N) | **RankAcc** | random-ranker floor | $/question |
|---:|---:|---:|---:|---:|---:|
| 4 | 19 | 19 | **1.000** | 19.0 | 0.101 |
| 8 | 32 | 33 | 0.970 | 33.0 | 0.142 |
| 16 | 43 | 43 | **1.000** | 32.8 | 0.138 |
| 32 | 46 | 48 | 0.958 | 23.6 | 0.161 |
| 64 | 51 | 54 | 0.944 | 16.0 | 0.169 |
| 128 | 52 | 56 | 0.929 | 10.2 | 0.233 |
| all | 52 | 56 | 0.929 | 9.3 | 0.224 |

Comparators on the same 60: MemPhant fused@10 **39**, packed@10 **37**, agentic
`grep` **57**.

§A.6 fixed stage 3 as *"the best 2 values of N from the sweep, plus N=64 as the
pre-committed anchor."* The two arms nominated, and the rule used to nominate
them — **the two curves §A.1 named as the deliverable, applied before any n=180
cell was seen**:

* **N=128 — the accuracy maximum.** 52/60 is the highest hit@10 on the sweep. It
  ties with `all`, and `all` is not a separate mechanism (the pool's median is
  124.5, so for most questions `all` *is* ~128); confirming both would buy one
  number twice. N=128 is taken and `all` is not.
* **N=16 — the accuracy-per-token maximum.** 5.2 hits per dollar against 5.0 at
  N=64 and 3.7 at N=128, at **$0.138/question**, and it is the only N besides 4
  where the agent recovered its ceiling **exactly**.
* **N=64 — the pre-committed anchor**, carried unchanged so stage 2 and stage 3
  are tied together and the result stays comparable to S4's arms.

Budget: $70.10 spent, ~$65 projected for stage 3, against the $180 ceiling.
