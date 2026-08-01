# S4 — the controls we never ran: agentic `grep` and no-memory dense RAG

**Date:** 2026-08-01 · **Branch:** `s4-controls` (base `main` @ `0e874da0`) ·
**Spend ceiling:** $80 · **Status at the time this section was written:
PREREGISTRATION. No cell has been seen.**

This file is written in two parts. **Part A is the preregistration and is
committed before any arm runs.** Part B is appended after the runs and never
edits Part A. If Part A and Part B disagree, Part A wins and the disagreement is
the finding.

---

# PART A — PREREGISTRATION (committed before any cell was seen)

## A.0 The question

*Can MemPhant, at its shipped default, beat a competent coding agent that has
`grep` and nothing else?*

This gates a separate ~$545 public-benchmark lane. The arms review
(`docs/build-log/2026-08-01-next-arms-and-sota-path.md`, Arms 1 and 2) ranked
these two controls **above** that lane on information per dollar, on the
argument: *if we cannot beat `grep` for $40, we will not beat a no-memory
baseline for $545.*

The published prior is against us:

| source | finding |
|---|---|
| SWE-Explore (arXiv:2606.07297) | HitFile@5 — agent-with-`grep` **0.667** · dense RAG **0.088** · BM25 **0.079** |
| ContextBench (arXiv:2602.05892) | plain-bash mini-SWE-agent **0.634** ctx-F1 beats every embedding-based system in the table |
| SWE-ContextBench Table 4 | Mem0 **24.24%** vs no-context **26.26%** — below doing nothing |
| CL-Bench (arXiv:2606.05661) | naive in-context learning beats every dedicated memory system |
| MemDelta (arXiv:2606.29914) | agent self-memory 42% vs 47% for basic retrieval; Mem0 = cloud RAG at 50× cost |

## A.1 Instrument

**Track R paraphrase bank, n = 180.** `benchmarks/data/track_r_paraphrase_golden.lock.json`,
`golden_sha256` and `corpus_sha256` both re-verified inside every runner via
`code_lane_run_memphant.verify_input_contract` before any arm executes.

Corpus: `nebius/SWE-rebench-openhands-trajectories` @ `35455389`, 495 attempts /
64,055 content events, CC-BY-4.0,
`corpus_sha256 = c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669`.

### A.1.1 The instrument's true MDE — computed here, not inherited

The instrument register lists Track R at **6.73pt**. **That ψ belongs to the
ORIGINAL bank and must not be used here.** The paraphrase bank's realized
discordance on its decisive contrast is b=29 / c=5 ⇒ n_d=34 ⇒ **ψ = 0.189**.

Recomputed for this lane with `scripts/instrument_power.py` at n=180, two-sided
exact McNemar, α=0.05, 80% power:

| ψ | MDE |
|---:|---:|
| 0.189 (realized) | **9.38pp** |
| 0.25 | 10.71pp |
| 0.30 | 11.72pp |

**Planning MDE for every contrast below: 9.38pp (≈ 17 of 180 questions).** The
realized ψ of each contrast is recomputed from that contrast's own discordant
pairs after the run and reported beside the planning value.

### A.1.2 The bound carried AHEAD of the numbers, not trailing them

The paraphrase bank **overshot its correction**. It bans identifier surfaces,
and real engineers name files. Absolute q→target lexical coverage brackets as:

```
paraphrase 0.1346   <   human 0.175–0.287   <   original 0.396
```

A `grep`-driven control is a **lexical** instrument. This bank is the
**lexically hardest** point in that bracket — harder than any human would pose.
Therefore, stated before any cell is seen:

> **Any MemPhant margin over the agentic control measured on this bank is an
> UPPER BOUND on the lexical-difficulty axis. Any agentic-control margin over
> MemPhant is a LOWER BOUND.**

In plain terms: a MemPhant win here may not survive on human-phrased queries; a
`grep` win here would only widen on them. This asymmetry is why a negative
result in this lane is decisive and a positive one is provisional.

## A.2 Arms — same pipeline stage, same haystack, same query string

A prior headline in this program was **VOIDED** for scoring one arm after
packing against another's plain ranked top-10. The equalization is therefore
stated explicitly and asserted in code.

| | arm | mechanism | haystack | query |
|---|---|---|---|---|
| **T** | MemPhant shipped default | `bm25code_dense` — `Bm25Code` lexical + local `bge-small-en-v1.5`, both defaults as of 2026-07-31 | its recall pool, bound to the golden's attempt by `bind_attempt_context` | `retrieval_query(golden)` |
| **C1** | **agentic search** | `anthropic/claude-opus-5` with `grep` / `read_event` / `list_events` in a bounded loop; **no MemPhant, no index, no embeddings** | the golden's attempt's raw events | same |
| **C2** | **no-memory dense RAG** | cosine top-10 over `bge-small-en-v1.5` vectors of raw events; no unit, no compilation, no bitemporal state, no fusion, no packing | the golden's attempt's raw events | same |
| **C0** | scoped BM25 (banked reference, not a control) | `code_lane_run_deterministic.py --scope attempt` | same | same |

**Haystack identity.** `code_lane_run_memphant.bind_attempt_context` binds one
scope/actor/agent lane per `attempt_id`, and evaluation recalls through
`evaluation_contexts[golden["provenance"][0]["attempt_id"]]`, so MemPhant's
candidate pool can never leave that attempt. C0/C1/C2 are scoped to exactly that
attempt. The attempt's full event set is a **superset** of MemPhant's pool.

**Stage identity.** Every arm terminates in ten bodies passed through the same
two functions — `gate_common.evidence_row(golden, bodies, 10)` and
`gate_common.provenance_hit(golden, bodies, 10)`. No arm is scored at a
different stage than another. This is asserted, not assumed: the comparison
script refuses to run unless every arm's report declares
`endpoint_contract == "gate_common.provenance_hit@10 over top-10 bodies"`.

**Query identity.** All arms call `code_lane_run_memphant.retrieval_query`,
which for this bank returns `golden["question"]` and raises if the gold answer
appears in it.

**Embedder held fixed.** Per the standing rule this program earned from
MemDelta's +6.2pp embedder-swap effect: T and C2 both use
`bge-small-en-v1.5`, no query/document prefix (`embeddings::prefix_text` returns
the text unchanged for the bge family — verified in source).

## A.3 Primary endpoint and banked treatment value

**Primary endpoint: `hits@10` = `gate_common.provenance_hit` at k=10 over the
arm's top-10 bodies, n=180, paired.**

Treatment value is **already banked and is not re-run**:
`~/.memphant-private/track-r-paraphrase/run-trunk/bm25code_dense-provenance.json`
⇒ **106 / 180 = 0.5889**. (The `113/180` figure quoted in the arms review is
`gold_fused_rank ≤ 10`, a *pre-packing* quantity; the stage-matched value is
106.) Reference C0 = **46 / 180 = 0.2556**.

Reader answer accuracy is preregistered as **secondary** and is run only if the
retrieval budget leaves room; it does not change any verdict below.

## A.4 The decision rule — fixed here, before any cell

Let Δ = rate(T) − rate(C), n_d = discordant pairs, p = two-sided **exact**
McNemar (conditional binomial, not χ²).

1. **n_d < 6 ⇒ write "NOT A MEASUREMENT" and the required n.** Never "a tie",
   never "no effect".
2. **Verdict A — MemPhant wins:** Δ ≥ +9.38pp **and** p < 0.05 **and** n_d ≥ 6.
3. **Verdict B — the control wins:** Δ ≤ −9.38pp **and** p < 0.05 **and** n_d ≥ 6.
4. **Verdict D — no detectable difference at this power:** n_d ≥ 6 and neither A
   nor B. Reported with the two-sided 95% CI on Δ and the statement *"the
   instrument cannot resolve an effect smaller than 9.38pp; effects inside that
   band are unmeasured, not absent."*

**The gate this lane owes the program:** the ~$545 SWE-ContextBench lane is
authorized **only under Verdict A on T vs C1.** Under B or D it is **not**
authorized by this lane, and the honest product framing moves toward governance
(supersession, forgetting, provenance) rather than retrieval.

## A.5 Mechanism-liveness gates — proven from each arm's OWN trace

*An inert mechanism and a neutral one produce the same number and mean opposite
things.* Each arm must prove it fired before its number is admissible:

- **C1** — per row, from the returned transcript: ≥1 tool call actually
  **executed** with a non-empty result, and every selected event sequence must
  exist in that attempt. Rows with zero executed tool calls, or with a selection
  that does not resolve, are **errors, not data**, and the run does not report
  until `reader_errors == 0`.
- **C2** — 384-dim vectors, ≥99% distinct vectors in the haystack (a constant
  embedder would score as a neutral one), and a recorded per-run hash of the
  concatenated query vectors.
- **T** — the banked provenance must show `lexical_scorer == "bm25-code"`,
  `embed_model == "small"`, and a non-empty per-question `channel_table` (both
  channels present). Asserted by the comparison script from the artifact.

## A.6 Budget symmetry — stated before the run, because it decides believability

SWE-Explore warns its own ground truth is *"structurally biased toward agentic
explorers, and compute budgets differ by orders of magnitude."* An unbounded
agent beats any bounded retriever. So:

- **C1 hard caps: 12 tool calls, 16 model turns, 24,000 completion tokens per
  question.** Tool results are truncated (`grep` ≤ 25 matches × 300 chars;
  `read_event` ≤ 6,000 chars; `list_events` ≤ 200 rows).
- **T costs zero LLM calls at recall.** C1's realized token cost is reported
  beside T's zero, and any C1 win is explicitly labelled as bought with LLM
  inference that T does not spend.
- C1 must select **at most 10** events, ranked. Selecting more is a contract
  violation and truncates to 10.

## A.7 Contamination probe (ONCU)

20 questions answered by `anthropic/claude-opus-5` with **no corpus access and
no tools**. On a *retrieval* endpoint parametric knowledge cannot manufacture a
provenance hit, so this probe cannot invalidate the primary endpoint; it is run
because the arms review requires it, it costs ~$0.10, and it bounds the
secondary reader endpoint. Result is reported; it does not gate A.4.

## A.8 Spend plan and the necessity test

| stage | what | ceiling |
|---|---|---:|
| 0 | stub round trip — full C1 loop against a loopback stub, zero paid calls | $0 |
| 0 | C2 in full (local `bge-small`, 180 questions) | $0 |
| 0 | C0 re-read from banked artifact | $0 |
| 1 | ONCU probe, 20 rows | ~$0.15 |
| 2 | **C1 pilot, 30 rows** — measures real cost/row and catches live-API defects | ~$6 |
| 3 | C1 full, remaining 150 rows | ~$30 |
| — | reserve | rest of $80 |

**The free gate that could make the paid arm worthless runs first:** C2 is $0 and
scores at the identical stage. If C2 alone already reaches or beats T, the
substrate's retrieval value is falsified for $0 and C1's role changes from
"decide" to "size the gap". C2 therefore runs before any money moves.

Provider pinned `only: ["anthropic"]`, `allow_fallbacks: false`,
`max_price = {prompt: 5.0, completion: 25.0}` USD/M — fetched live from the
OpenRouter catalogue on 2026-08-01 (`anthropic/claude-opus-5`: prompt
`0.000005`, completion `0.000025`, cache-read `0.0000005`). An upstream price
change fails the call rather than silently costing more.

## A.9 Lineage

Every artifact carries git HEAD, branch, dirty flag, the sha256 of every input,
and — for T — the banked `binaries.sha256` of the served MemPhant binaries. An
artifact without lineage did not happen.

---

# PART B — RESULTS (appended after the runs; Part A above is unedited)

## B.0 The answer, in one table

**Instrument:** Track R paraphrase, n=180. **Endpoint:**
`gate_common.provenance_hit` at k=10 over each arm's top-10 bodies. **Haystack:**
the golden's bound attempt, identical for all four arms. **Query:**
`retrieval_query(golden)`, identical for all four arms.

| arm | hits@10 | rate | hits@5 | LLM calls at recall | spend |
|---|---:|---:|---:|---:|---:|
| **C1 — agentic `grep` control** | **174 / 180** | **0.9667** | **174 / 180** | 718 | **$25.93** |
| T — MemPhant `bm25code_dense` (shipped default) | 106 / 180 | 0.5889 | 73 / 180 | **0** | **$0** |
| C0 — scoped BM25 (banked reference) | 46 / 180 | 0.2556 | — | 0 | $0 |

Ceiling for every arm on this bank: **178 / 180** (§B.5).

| contrast | b | c | n_d | Δ | exact McNemar p | realized ψ | realized MDE | **verdict** |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **T vs C1 (agentic `grep`)** | **1** | **69** | 70 | **−37.78pp** | **1.20e−19** | 0.389 | 13.34pp | **B — the control wins** |
| T vs C0 (scoped BM25) | 66 | 6 | 72 | +33.33pp | 7.26e−14 | 0.400 | 13.54pp | A — MemPhant wins |

**Read the b-cell.** Across 180 questions there is **exactly one** on which
MemPhant retrieves the gold and the `grep` agent does not
(`track_r_par_024`). There are **69** the other way.

**The answer to the question this lane was opened to settle:
no. MemPhant does not beat an agent with `grep`. It loses by 37.8 points on its
own bank, at its own shipped default, on the same haystack, at the same stage,
with the same query string.**

## B.1 The verdict, and the gate it closes

§A.4 was committed before any cell was seen. Applied verbatim:
n_d = 70 ≥ 6, Δ = −37.78pp ≤ −9.38pp, p = 1.20e−19 < 0.05 ⇒ **Verdict B.**

Per §A.3, **the ~$545 SWE-ContextBench lane is NOT authorized by this lane.**
The arms review's premise was *"if we cannot beat `grep` for $40, we will not
beat a no-memory baseline for $545."* We could not, and it cost $25.98.

This also settles the plan's §D4 fallback in the direction the plan feared:
**retrieval is not this product's contribution on coding work.** The honest
product is governance — supersession, forgetting, provenance, the file plane —
and this lane's result should be read as evidence *for* that pivot, not as a
number to improve.

## B.2 The result is not an artifact of budget, and the arm's own trace says so

§A.6 fixed hard caps precisely so a win could not be dismissed as an unbounded
agent beating a bounded retriever. The caps turned out **not to bind**:

| cap | value | realized |
|---|---:|---:|
| tool calls per question | 12 | **mean 3.99, median 4, max 9** |
| model turns | 16 | never reached |
| completion tokens per question | 24,000 | mean ~700 |
| selection size | 10 | **mean 4.51** |

The agent did not spend its budget. It also did not need depth: **hits@5 equals
hits@10 exactly** (174 both), so every hit it got, it got in its first five
picks. Loosening the caps could not have produced this margin, and tightening
them would not have removed it.

**Miss attribution, post-hoc from the transcript** (the gold sequence never
entered the agent's context): a `grep`/`read_event` result surfaced the gold
event on **176 / 180** questions, and the agent then selected it on **175**. So
its search located the evidence 97.8% of the time and its ranking dropped it
once. The remaining losses are the three provider refusals of §B.4.

**What the control does spend, and MemPhant does not:** 4,554,054 prompt tokens
and 126,460 completion tokens, 718 model calls, **$25.93 — $0.144 per
question.** MemPhant answers with **zero** LLM calls at recall. That is the
whole of MemPhant's remaining advantage on this lane and it is a cost-and-latency
advantage, not an accuracy one. It is a real product position; it is not the one
the program has been arguing for.

## B.3 The bound carried ahead of the numbers now cuts against us

§A.1.2, written before any cell: *"Any MemPhant margin over the agentic control
here is an UPPER BOUND; any agentic-control margin over MemPhant is a LOWER
BOUND."*

The margin went to the control. Therefore **−37.78pp is a lower bound.** This
bank withholds the identifier surfaces a `grep` control feeds on
(q→target coverage 0.1346 against human 0.175–0.287), which is the hardest
regime we could have put it in — and it still won 174 to 106. On
human-phrased queries, which name files and symbols, the gap can only widen.

The ONCU probe (§B.6) points the same way: it bounds a bias that inflates the
**control**, and even granting every one of its questions to that bias the
verdict does not move.

## B.4 Three rows the pinned provider refuses — reported, not smoothed

`track_r_par_057`, `track_r_par_126`, `track_r_par_155` return
`finish_reason="content_filter"`, 2–8 completion tokens and an empty message on
**every** retry at temperature 0 — including after two protocol nudges and under
`tool_choice="required"`, and with the per-call token cap raised from 2,000 to
8,000 to rule out truncation. The pinned provider refuses them. This is a
provider limit, not a harness defect and not a search failure.

The standing rule is to drive errors to zero before reporting. **Here that is
not achievable without unpinning the provider, which is a preregistered guard,
so the rule is honoured the other way: the unscoreable rows are scored as MISSES
for the arm that could not produce them** — the assumption most favourable to
the treatment — and the complete-case analysis is reported beside it.

| analysis | n | T | C1 | Δ | p | verdict |
|---|---:|---:|---:|---:|---:|---|
| **headline** (refusals = miss for C1) | 180 | 106 | 174 | −37.78pp | 1.20e−19 | B |
| complete case (refusals dropped from both arms) | 177 | 106 | 174 | −38.42pp | 1.20e−19 | B |

The headline is the *smaller* of the two margins. Nothing in the verdict turns
on the handling.

## B.4b Robustness: even at MemPhant's most generous stage, it still loses

The headline pairs like with like — both arms at ten bodies. MemPhant also has a
*pre-packing* quantity, `gold_fused_rank <= 10`, which is **113/180** and which
the arms review quotes. Packing costs it 7 questions. That number is **not**
stage-matched to the control and must not be the headline — this program voided
a result for exactly that mismatch — but it bounds how much of the deficit
packing could possibly explain:

| contrast | T | C1 | b | c | n_d | Δ | p | realized MDE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| headline, stage-matched (packed@10) | 106 | 174 | 1 | 69 | 70 | −37.78pp | 1.20e−19 | 13.34pp |
| **generous to T, stage-MISMATCHED** (fused@10 vs packed@10) | **113** | 174 | 1 | 62 | 63 | **−33.89pp** | 1.39e−17 | 12.66pp |

**Handing MemPhant its own best pre-packing number and comparing it against the
control's honest one still loses by 33.9 points, and the b-cell is still 1.**
Packing is not the explanation, and no packing fix reaches this gap: the whole
of rung 7 is worth 7 questions against a 68-question deficit.

## B.5 A 2-question dead zone in the instrument, found free and verified symmetric

`track_r_par_028` and `track_r_par_066` carry required spans that appear in **no
event of their own attempt** — the corpus clips events at 4,000 characters and
these spans sit past the clip. No arm can reach them: verified false for
MemPhant (`gold_in_pool` false, `gold_fused_rank` null), for scoped BM25, and
for a raw-event oracle handed the declared gold event itself.

**The effective ceiling on this bank is 178/180 (0.9889) for every arm equally.**
It is symmetric, so it biases no contrast; it does mean the agentic control at
174/180 is within **4 questions of the achievable ceiling.**

## B.6 ONCU contamination probe

20 questions, `anthropic/claude-opus-5`, no corpus and no tools, seed 20260801.
**2 / 20 answered correctly with no evidence** (`track_r_par_051`,
`track_r_par_018`) — both cases where the question's phrasing lets the answer be
reconstructed, not deep memorisation; 10/20 correctly said `UNKNOWN`. Cost
$0.0512.

On a *retrieval* endpoint this cannot manufacture a provenance hit, but it can
let a `grep` control search for the **answer** instead of the evidence — a bias
that inflates C1. At 10% it cannot come close to explaining a 69-vs-1 discordance.

## B.7 Mechanism liveness — proved from each arm's own trace

| arm | proof | source |
|---|---|---|
| **T** | `lexical_scorer="bm25-code"`, `embed_model="small"`, and **both** `lexical` and `vector` entries present in the per-candidate `channel_table` on all 180 questions | asserted by `s4_controls_compare.assert_treatment_liveness`, which exits non-zero if either channel is absent |
| **C1** | 177/180 rows carry ≥1 executed tool call; **0** rows have an unresolved selection; the 3 exceptions are the provider refusals, recorded with their `finish_reason` | the arm's own `liveness` block and per-row transcript |

The treatment artifact is `run-fusion/fusion_probe-provenance.json` rather than
`run-trunk/bm25code_dense-provenance.json` for one reason: the two are
**byte-identical on `hit_at_10` for all 180 questions**, and only the former
banks the `channel_table` that proves both fusion channels fired. Choosing the
artifact that can prove its own mechanism is the point of the rule.

## B.8 Preregistration deviations — disclosed, not smoothed

**1. §A.8's ordering was not followed.** A.8 said C2 (free) runs before any money
moves. C2 was running when the paid arms started, and had not finished. The
reason: the host went to load ~130–165 under six sibling lanes and C2's local
embed fell to ~3 docs/s, which would have idled the paid arm for over an hour.
On inspection **the ordering could not have saved money anyway** — the brief
requires C1 regardless of C2's value, so no C2 result would have cancelled C1's
spend. What A.8 was protecting (a free falsifier is not skipped, and its number
is in hand before the expensive one is interpreted) is preserved: C2 is not
cancelled. But the letter of A.8 was not followed and it is recorded here as a
deviation rather than claimed as compliance.

**2. `MAX_TOKENS_PER_CALL` was raised 2,000 → 8,000 mid-lane**, while diagnosing
the three refusals, to rule out truncation as their cause. It is not one of
A.6's preregistered caps (the per-question ceiling of 24,000 completion tokens
is, and never bound at a realized mean of ~700). It changed no scored row: the
three rows still refuse, and no other row came near either limit.

**3. A protocol nudge and `tool_choice="required"` escalation were added mid-lane**
after the pilot showed prose turns. Both are outcome-independent, carry no task
information, and apply identically to any row that hits them. Neither rescued
the three refusals.

## B.9 Arm 2 (no-memory dense RAG) — status

Still running at the time this section was written: `bge-small-en-v1.5` over
21,629 raw events, in-process, at ~3 documents/second on a host at load ~150.
It is $0 and its result changes no verdict above — C1 already answers the
question the lane was opened for. Its artifact and contrast will be appended to
`docs/build-log/artifacts/s4-controls/` when it lands. Reporting the lane's
answer without it is not a shortcut: C2 tests a different and weaker claim
("the substrate beats a 60-line script"), and the arm that tests the claim the
program actually rests on has reported.

## B.10 Spend ledger

| stage | what | reported | basis |
|---|---|---:|---|
| 0 | stub round trip (full adapter contract, no network) | $0 | — |
| 0 | C2 dense RAG (local `bge-small`) | $0 | — |
| 0 | C0 re-read from banked artifact | $0 | — |
| 1 | ONCU probe, 20 rows | $0.0512 | pinned price × reported tokens |
| 2 | C1 pilot, 30 rows | $4.31 | ″ |
| 3 | C1 remaining 152 rows + 3 refusal retries | $21.62 | ″ |
| | **total** | **$25.98** | **ceiling $80** |

Unsettled liability: the figures are **pinned-catalogue-price × reported
tokens**, an upper bound, not OpenRouter's settled cost. Generation ids are
banked in the arm's provenance for reconciliation.

Provider pin held for every paid call: `only: ["anthropic"]`,
`allow_fallbacks: false`, `max_price = {prompt: 5.0, completion: 25.0}` USD/M,
fetched live from the OpenRouter catalogue on 2026-08-01. No call was served by
a fallback provider and no price change occurred.

## B.11 What this does and does not license

**Licensed.** (1) Do not authorize the ~$545 SWE-ContextBench lane on the
strength of MemPhant's coding retrieval. (2) Stop treating scoped BM25 as the
control: beating it by +33pp and losing to `grep` by −38pp on the same bank, at
the same stage, shows what that comparator was worth. (3) Any future coding
retrieval arm must carry the agentic control in the same table, or it is not a
measurement.

**Not licensed.** This artifact is `decisional: false` and says so in its
contract. The bank **fails its own preregistered leakage bar**
(`leak_concentration_le_1_50` false at 2.018, `bar_passed` false) and its corpus
licence is a card assertion with no LICENSE blob pinned. So this does not
promote or kill a mechanism by itself. It also does not measure answer accuracy,
end-to-end latency, cost at scale, or anything about governance — supersession,
forgetting, provenance — which is the surface this result points at and which no
control here touched.

**And it does not say MemPhant is worthless.** It says that on *this* lane, at
*this* endpoint, retrieval quality is not where the value is, and that the value
that remains — 0 LLM calls against $0.144 per question — is a cost-and-latency
claim that this lane did not set out to make and did not measure properly.
