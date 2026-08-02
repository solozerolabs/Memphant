# SDD S4 — agentic-`grep` and dense-RAG controls

**Source branch:** `s4-controls` (base `main` @ `0e874da0`) · **locally merged into
integration `main` at `248195d8`; not pushed** ·
**Preregistration:** `docs/build-log/2026-08-01-agentic-search-controls.md` §A,
committed at `6912e35f` **before any cell was seen**.

---

## 1. What was asked, and what decides it

*Can MemPhant, at its shipped default, beat a competent coding agent that has
`grep` and nothing else?* The answer gates a separate ~$545 public-benchmark
lane, on the arms review's argument that **if we cannot beat `grep` for $40 we
will not beat a no-memory baseline for $545.**

Three arms, one endpoint, one haystack, one query string:

| arm | mechanism | cost at recall |
|---|---|---|
| **T** MemPhant `bm25code_dense` | `Bm25Code` + local `bge-small-en-v1.5`, both shipped defaults | 0 LLM calls |
| **C1** agentic search | `anthropic/claude-opus-5` with `grep`/`read_event`/`list_events`, bounded loop | paid, budgeted |
| **C2** no-memory dense RAG | cosine top-10 over `bge-small` vectors of raw events | $0 |
| **C0** scoped BM25 (reference) | banked `code_lane_run_deterministic.py --scope attempt` | $0 |

Endpoint for all four: `gate_common.provenance_hit` at k=10 over the arm's
top-10 bodies, Track R paraphrase bank, n=180.

---

## 2. Results

| arm | hits@10 | rate | hits@5 | LLM calls at recall | spend |
|---|---:|---:|---:|---:|---:|
| **C1 — agentic `grep`** | **174 / 180** | **0.9667** | **174 / 180** | 718 | $25.93 |
| T — MemPhant `bm25code_dense` | 106 / 180 | 0.5889 | 73 / 180 | **0** | $0 |
| C0 — scoped BM25 (reference) | 46 / 180 | 0.2556 | — | 0 | $0 |

Ceiling on this bank, for every arm: **178 / 180**.

| contrast | b | c | n_d | Δ | exact p | realized ψ / MDE | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| **T vs C1** | **1** | **69** | 70 | **−37.78pp** | 1.20e−19 | 0.389 / 13.34pp | **B — the control wins** |
| T vs C0 | 66 | 6 | 72 | +33.33pp | 7.26e−14 | 0.400 / 13.54pp | A — MemPhant wins |

**Answer: no. MemPhant does not beat an agent with `grep`.** It loses by 37.8
points on its own bank, at its shipped default, on the same haystack, at the same
stage, with the same query string — while beating the comparator the program has
been using (scoped BM25) by +33.3 points on that same bank. There is exactly
**one** question in 180 where MemPhant hits and `grep` misses.

**Consequence: the ~$545 SWE-ContextBench lane is NOT authorized by this lane**
(§A.3, fixed before any cell).

**Not a budget artifact.** The §A.6 caps never bound — mean 3.99 tool calls
against 12, mean 4.51 selections against 10, ~700 completion tokens against
24,000 — and **hits@5 equals hits@10 exactly**, so every hit came in the first
five picks. Post-hoc: search surfaced the gold event on 176/180 and the agent
selected it on 175.

**Not an upper bound.** §A.1.2, committed before any cell, fixed that a control
margin on this identifier-withholding bank is a **lower** bound; on
human-phrased queries the gap can only widen.

**What MemPhant still has here is cost, not accuracy:** 0 LLM calls versus
$0.144 per question. That is a defensible product position and it is a different
one from the program's current claim.

Handled honestly rather than smoothed: three provider content-filter refusals
scored as misses for the control (with an n=177 complete-case sensitivity,
−38.42pp, same verdict); a 2-question dead zone in the bank verified symmetric
across arms; an ONCU probe at 2/20 whose bias inflates the control; and four
preregistration deviations listed in Part B §B.8, including a liveness gate of
mine that was wrong and is corrected there rather than quietly restated.

Full numbers, transcripts and lineage: `docs/build-log/2026-08-01-agentic-search-controls.md`
Part B and `docs/build-log/artifacts/s4-controls/analysis.json`.

**Arm 2 (dense RAG, $0) did not land** and no number is reported for it. Its own
preregistered liveness gate killed it after ~90 minutes of embedding, and the
gate was wrong: it measured corpus duplication (1,330 byte-identical events
capped it at 0.9385) rather than embedder health. Fixed to be stricter on the
property it was written to protect, and vectors are now checkpointed before they
are gated. See Part B §B.9 and deviation 4. It tests a weaker claim and no C2
value changes the verdict above.

**Total spend: $25.98** against an $80 ceiling (ONCU $0.0512 + agentic $25.93).
Figures are pinned-price × reported tokens — an upper bound, not settled cost;
generation ids are banked for reconciliation.

---

## 3. Method notes that decide whether the numbers mean anything

**Same stage.** Every arm terminates in ten bodies graded by the identical
`gate_common.provenance_hit(golden, bodies, 10)`. `s4_controls_compare.py`
refuses to pair two arms that do not both declare
`gate_common.provenance_hit@10 over top-10 bodies`, or, for the two banked
runners that predate the string, whose `k` is not 10. A headline in this program
was voided for scoring one arm after packing against another's plain ranked
top-10; this is the mechanical guard against a repeat.

**Same haystack.** MemPhant's recall is bound to the golden's attempt by
`bind_attempt_context`, so its pool can never leave that attempt. C0, C1 and C2
rank exactly that attempt's raw events — a superset of MemPhant's own pool.

**Same query string.** All arms call `code_lane_run_memphant.retrieval_query`,
which raises if the gold answer appears in the query.

**Mechanism liveness from each arm's own trace, never from the flag.** T must
show both `lexical` and `vector` channels present in its per-candidate
`channel_table`; C1 must show an executed tool call and a selection that
resolves to real event sequences. C2's gate was written wrong and is corrected
in §B.9 — it asserted a distinct-vector share that the corpus's own duplication
capped below the bar, so it destroyed the run it existed to certify.

**Power computed, not inherited.** The instrument register lists Track R at
6.73pt; that ψ belongs to the **original** bank. The paraphrase bank's realized
ψ is 0.189 ⇒ **MDE 9.38pp at n=180**, recomputed with `scripts/instrument_power.py`.
Each contrast additionally reports its own realized ψ and MDE.

**The bound carried ahead of the numbers.** The paraphrase bank overshot its
correction: it bans identifier surfaces, and real engineers name files. Absolute
q→target coverage brackets as paraphrase 0.1346 < human 0.175–0.287 < original
0.396. A `grep` control is a lexical instrument, so this bank is the hardest
point on that axis for it. **Any MemPhant margin over C1 here is an upper bound;
any C1 margin over MemPhant is a lower bound.**

---

## 4. Files

| path | what |
|---|---|
| `docs/build-log/2026-08-01-agentic-search-controls.md` | preregistration (Part A) + results (Part B) |
| `docs/build-log/artifacts/s4-controls/analysis.json` | the paired comparison, registered evidence artifact |
| `scripts/s4_controls_common.py` | shared endpoint contract, lineage, haystack rule |
| `scripts/code_lane_run_agentic_control.py` | C1, with a $0 stub engine |
| `scripts/code_lane_run_dense_control.py` | C2 |
| `scripts/s4_controls_compare.py` | applies §A.4 verbatim |
| `scripts/s4_oncu_probe.py` | contamination probe |
| `scripts/s4_controls_run.sh` | staged runner: oncu / pilot / full / compare |
| `tests/test_s4_controls.py` | stage identity, decision rule, budget caps |
