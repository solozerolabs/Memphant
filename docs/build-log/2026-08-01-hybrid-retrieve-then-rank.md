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

---

# PART B — RESULTS (appended after the runs; Parts A and A2 above are unedited)

## B.0 Two bounds, first, because they decide how far this travels

**1. This is one attempt at a pool median of 124.5 units.** MemPhant's whole
candidate pool is already nearly the whole attempt, which is exactly what makes
S4's `list_events` brute force affordable at ~4 tool calls and $0.144/question.
**Every `N*` below is `N*` measured at a 124-item haystack.** At repo scale or
across sessions `list_events` has no analogue and enumeration is infeasible —
that is the regime in which a narrowing retriever would pay for itself, and
**this bank cannot test it.** Nothing here is extrapolated past it.

**2. The bank withholds the identifier surfaces a `grep` control feeds on**
(q→target coverage 0.1346 against human 0.175–0.287). S4's rule carries over
unchanged: **any margin `grep` holds here is a LOWER bound**, and it holds one
below.

## B.1 The answer, in one table

**Instrument:** Track R paraphrase, n=180. **Endpoint:**
`gate_common.provenance_hit@10 over top-10 bodies` — the identical string S4's
arms declare. **Query:** `retrieval_query(golden)`, banked per question in the
pool dump and identical for every arm.

| arm | hits@10 | rate | **its own ceiling** | **RankAcc** | random-ranker floor | $/question |
|---|---:|---:|---:|---:|---:|---:|
| agentic `grep` (S4, banked; haystack = raw events, a SUPERSET) | **174** | .9667 | 178 | — | — | $0.144 |
| **H(N=128)** | **155** | .8611 | 168 | .9226 | 31.9 | $0.230 |
| **H(N=64)** | **154** | .8556 | 164 | .9390 | 48.8 | $0.191 |
| **H(N=16)** | **120** | .6667 | 127 | .9449 | 94.3 | $0.151 |
| MemPhant fused@10 (stage-matched retriever comparator) | 112 | .6222 | 169 | — | — | **$0** |
| MemPhant packed@10 (shipped default) | 105 | .5833 | 169 | — | — | **$0** |

| contrast | b | c | n_d | Δ | exact McNemar p | realized MDE | **verdict** |
|---|---:|---:|---:|---:|---:|---:|---|
| **H(64) vs MemPhant fused@10** | **48** | 6 | 54 | **+23.33pp** | **3.26e−09** | 11.72pp | **H wins** |
| H(64) vs MemPhant packed@10 | 55 | 6 | 61 | +27.22pp | 5.38e−11 | 12.46pp | H wins |
| H(128) vs MemPhant fused@10 | 50 | 7 | 57 | +23.89pp | 4.24e−09 | 12.04pp | H wins |
| H(16) vs MemPhant fused@10 | 15 | 7 | 22 | +4.44pp | 0.134 | 7.49pp | no detectable difference at this power |
| **H(64) vs agentic `grep`** | **0** | 20 | 20 | −11.11pp | 1.91e−06 | 7.11pp | the comparator wins |
| H(128) vs agentic `grep` | 0 | 19 | 19 | −10.56pp | 3.81e−06 | 6.92pp | the comparator wins |

**Agent ranking recovers essentially all of the ranking headroom.** MemPhant's
own fuser converts a pool containing the gold on 164/180 questions (at N=64) into
112 hits. The same pool, re-ranked by an agent, yields **154**. That is 42 of the
52 available questions, b=48 against c=6, p=3.3e−9.

**And it still loses to `grep`, by 20 questions, at 1.32× `grep`'s cost.** The
b-cell is **0**: across 180 questions there is not one on which the hybrid
retrieves the gold and the `grep` agent does not.

## B.2 RankAcc does not fall with N. It is 1.000, everywhere.

§A.1 predicted RankAcc would decline with N through attention dilution, giving
`hit@10(N) = Coverage(N) × RankAcc(N)` an interior maximum. **The prediction is
wrong, and the reason it looked right is a confound worth naming.**

Decomposing every miss at every confirmed N:

| N | misses | out-of-view (retriever) | in-view but ranked out | of those, provider refusals | **genuine ranking failures** |
|---:|---:|---:|---:|---:|---:|
| 16 | 60 | 53 | 7 | 7 | **0** |
| 64 | 26 | 16 | 10 | 10 | **0** |
| 128 | 25 | 12 | 13 | 13 | **0** |

**Conditional on being allowed to answer at all, the agent placed the gold in its
top ten on 100% of the questions where MemPhant handed it the gold — at N=16, at
N=64 and at N=128 alike.** Every single apparent ranking failure is a row the
pinned provider refused (§B.4). The apparent RankAcc decline from .945 to .923 is
**entirely** the refusal count rising with N, and it rises with N only because
coverage rises with N: more questions have gold in view, so more refusals land on
a question that had gold in view.

There is therefore **no cliff and no slope** up to N=128 on this haystack — and
this is the one finding here with a chance of transferring, because it is a
statement about how many candidates an agent can usefully judge rather than about
this corpus. What it says is: **at 128 candidates of ~1,077 characters each, the
limit has not been reached.** It does not say where the limit is. Nothing was
measured past 128, and the pool's own median of 124.5 is why.

The random-ranker floor is what makes this readable. At N=64 a uniform draw of
ten from the sixty-four scores **48.8**; the agent scored 154 of a possible 164.
At N=128 the floor is 31.9. The agent is ranking, not sampling.

## B.3 So `N*` is a cost question, not an accuracy question

Because RankAcc is flat at 1.000, `hit@10(N) = Coverage(N)` exactly (less
refusals), and **Coverage is free to compute and already saturating**. The
interior maximum §A.1 hoped for does not exist: accuracy is monotone in N and
flattens where coverage does.

| N | hits@10 | Coverage(N) | prompt tokens/q | $/q | ×`grep` | hits per dollar |
|---:|---:|---:|---:|---:|---:|---:|
| 16 | 120 | 127 | 23,470 | 0.151 | 1.05× | 4.4 |
| **64** | **154** | 164 | 31,377 | **0.191** | 1.32× | **4.5** |
| 128 | 155 | 168 | 39,273 | 0.230 | 1.60× | 3.7 |

**N=64 is the operating point on this haystack.** N=128 buys **one** additional
question for 21% more cost per question; N=16 saves 21% and gives up 34
questions. The knee is at 64 because that is where MemPhant's coverage curve
flattens — 164 → 168 between N=64 and N=128 — not because of anything about the
ranker.

**The accuracy-per-token peak and the accuracy peak coincide at N=64**, which is
not what §A.1 expected and follows directly from RankAcc being flat.

## B.4 The refusals — 14 questions, and they cost this arm ~10 of them

`finish_reason="content_filter"`, empty message, on every retry at temperature 0
including under two protocol nudges and `tool_choice="required"`. **11 questions
refuse at all three confirmed N, and the union across all runs is 14.** S4's
three (`track_r_par_057`, `_126`, `_155`) are a **subset** of this lane's 14.

They are scored **MISSES for the hybrid** — the assumption least favourable to
it — under an explicit `--refusals-as-miss` that launders no other error class
and names every affected `question_id` in the analysis artifact. Unpinning the
provider would clear them and would also break a preregistered guard, so the
"errors are not results" rule is honoured the other way rather than by weakening
the pin.

**This is a real deviation and it runs against this arm.** S4's agentic control
saw 3 refusals on the same bank, same model, same provider pin; this arm sees 14.
The arms differ only in prompt and tool surface, so the extra 11 are this
harness's, not the bank's. At S4's refusal rate H(64) would score roughly
**161–164/180** rather than 154 — still short of `grep`'s 174, so **no verdict in
§B.1 turns on it**, but the gap to `grep` is overstated by about half its size
and that is said here rather than left for a reader to find.

## B.5 Where the residual gap to `grep` actually lives

At N=64, H misses 26 questions. **Not one of them is a ranking failure.**

* **16 — out-of-view.** Gold is not in MemPhant's top 64. Fixable only by better
  retrieval; no ranker, at any budget, can select what it was not shown. Of
  these, **11 are out of MemPhant's pool entirely at any rank** — the hard
  ceiling of §A.2, unmovable without changing recall itself.
* **10 — provider refusals** (§B.4). Not a search failure and not a ranking
  failure.
* **0 — ranking failures.**

**§A.2's question 3 is answered, and the answer is clean: the residual gap to
`grep` is not the agent's ranking. It is entirely retrieval coverage plus a
provider limit.**

## B.6 The verdict on "MemPhant narrows, the agent ranks"

**As a fix for MemPhant's ranking: it works, decisively, and it is the largest
retrieval gain this program has measured on this bank.** +23.33pp over the
stage-matched fused top-10, +27.22pp over the shipped default, b=48 / c=6,
p=3.3e−9. The fuser is leaving 42 questions on the table that are already in its
own pool, and an agent recovers all of them.

**As a product, on this haystack: no.** It costs $0.191/question against `grep`'s
$0.144 and scores 154 against 174, with a b-cell of **0**. On a 124-item
haystack there is no reason to narrow first, because not narrowing is cheaper and
strictly better.

**The two findings are not in tension, and the honest reading is the third one:**
what this lane actually measured is that **MemPhant's recall is good and its
fusion is bad.** Coverage is 0.9389 — within 2.8pp of what `grep` realizes — and
the shipped fuser converts that into 0.5833. An LLM re-ranker closes 81% of that
gap but costs more per question than the brute force it was meant to replace,
*at this scale*. The interesting quantity is therefore not the agent at all:
**it is that a perfect ranker over MemPhant's existing pool would score
164/180 = 0.911 at $0**, and the shipped fuser gets 105. That is a cheap-ranker
problem — a cross-encoder, a better fusion, a learned reranker — and this lane
has now bounded exactly how much such a thing is worth: **+59 questions, and not
one point more**, because 164 is the pool's ceiling at N=64.

**What this lane does NOT license.** It does not authorize the hybrid as a
shipped architecture — the cost comparison that would justify it cannot be made
on a 124-item haystack, and §B.0 says so ahead of the number. It does not move
any default. It does not measure answer accuracy, latency, or anything at repo
scale. And it does not rescue S4's verdict: `grep` still wins, by 20 questions
instead of 68.

## B.7 Mechanism liveness — pool containment, proved from each arm's own output

| check | N=16 | N=64 | N=128 |
|---|---:|---:|---:|
| `pool_containment_violations` | **0** | **0** | **0** |
| rows with an unresolved selection | 0 | 0 | 0 |
| out-of-range `read_item` requests | 0 | 0 | 0 |
| `raw_event_access` | false | false | false |
| tools offered | `grep_pool`, `list_pool`, `read_item`, `select` | — | — |
| mean tool calls | 7.12 | 6.9 | 6.4 |

Containment is checked per **returned body**, not per tool call: every body each
arm returned was matched against the exact set of bodies that question's agent
was handed, and the runner exits non-zero rather than writing a score on any
mismatch. There is no `list_events`, no filesystem, and no tool that can name an
event, a sequence or a path. **If the agent could have reached past the pool this
would be S4 re-run and the comparison void; it could not, and that is asserted
rather than assumed.**

The retriever half is live from its own run's provenance: `lexical_scorer =
bm25-code`, `embed_model = small`, 21,630 events compiled, and this run's own
packed top-10 reproduces the banked shipped-default arm at **105/180 against
106/180** — a one-question difference across independent scratch databases and
two different heads, with the **coverage curve reproducing exactly**
(169/180 in-pool, 164 at N≤64, 127 at N≤16).

## B.8 Preregistration deviations — disclosed, not smoothed

1. **§A.1's central prediction was wrong.** RankAcc does not fall with N; it is
   1.000 at every N tested. Part A is unedited and the disagreement is §B.2.
2. **`--refusals-as-miss` is a mid-lane addition** (§B.4). It admits exactly one
   deterministic provider error class, scores it against the hybrid, names every
   affected question, and is refused for any other error kind — pinned by a test.
3. **§A.6 said "the best 2 values of N plus the N=64 anchor".** The two best by
   raw hit@10 were N=128 and N=`all`, which tie and are not distinct mechanisms
   at a pool median of 124.5. The selection rule actually used — accuracy maximum
   plus accuracy-per-token maximum — is recorded in **Part A2, committed after
   the sweep and before any n=180 cell was seen**, together with why.
4. **The pool-dump run was killed and relaunched** after it was found sitting in
   the launching shell's process group (PGID ≠ PID) eight minutes before a
   60-minute lifecycle boundary that had already destroyed a sibling lane's
   multi-hour chain. It was relaunched under `scripts/detach_run.py`; ~25 minutes
   of ingest was re-done. No cell was affected — no paid call had been made.
5. **A comment in `s8_hybrid_analyze.py` wrongly attributed a `required_n`
   argument-transposition to S4's compare script.** S4 does not contain that
   defect; I inferred it from a truncated read. Corrected in `dde3a949`, and the
   hazard is now pinned by a test at this lane's own call site instead of
   asserted in prose about someone else's file.

## B.9 Spend ledger

| stage | what | reported | incremental |
|---|---|---:|---:|
| 0 | pool dump — local server, local embedder, 21,630 events | $0 | $0 |
| 1 | stub round trip — full arm contract, no network | $0 | $0 |
| 2 | coarse sweep, 7 values of N × 60 questions | $70.10 | $70.10 |
| 3 | confirm N=16 (56 rows carried from the sweep, not re-billed) | $27.11 | **$18.94** |
| 3 | confirm N=64 (″) | $34.31 | **$24.26** |
| 3 | confirm N=128 (″) | $41.40 | **$27.50** |
| | **total actually spent** | | **$140.80** |

Ceiling $180. The `reported` column double-counts carried rows by construction —
each confirmation's total includes the sweep tokens it inherited — so the
`incremental` column is the ledger and the difference is stated rather than
netted silently. Basis: **pinned catalogue price × reported tokens**, an upper
bound, not settled cost; generation ids are banked for reconciliation. The price
pin (`anthropic/claude-opus-5`, prompt 0.000005, completion 0.000025) was
re-fetched live from the OpenRouter catalogue before the first paid call and
matched. `only: ["anthropic"]`, `allow_fallbacks: false`,
`max_price = {prompt: 5.0, completion: 25.0}` held for every paid call.

## B.10 Artifacts

* `docs/build-log/artifacts/s8-hybrid/analysis-confirm.json` — the decisive
  analysis, n=180, with its generated `evidence_contract` (`decisional: false`).
* `docs/build-log/artifacts/s8-hybrid/analysis-sweep.json` — the screening
  sweep, stamped NOT A MEASUREMENT on every contrast.
* `docs/build-log/artifacts/s8-hybrid/sweep-subset.json` — the 60 question ids,
  their seed and their draw method, committed before any cell.
* Private, gitignored: `~/.memphant-private/track-r-paraphrase/run-s8/` — the
  pool dump (31 MB), every arm's provenance and evidence, and the per-question
  checkpoints.
