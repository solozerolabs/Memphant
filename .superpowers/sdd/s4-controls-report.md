# SDD S4 — agentic-`grep` and dense-RAG controls

**Branch:** `s4-controls` (base `main` @ `0e874da0`, **not merged**) ·
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

*(Part B of the build log carries the full numbers, the transcripts and the
lineage. This section is the summary the caller reads.)*

See `docs/build-log/2026-08-01-agentic-search-controls.md` Part B and
`docs/build-log/artifacts/s4-controls/analysis.json`.

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
`channel_table`; C2 must produce 384-dim, ≥99%-distinct vectors; C1 must show an
executed tool call and a selection that resolves to real event sequences.

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
