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
