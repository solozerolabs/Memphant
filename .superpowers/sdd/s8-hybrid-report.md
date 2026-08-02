# S8 — retrieve-then-rank: MemPhant narrows, the agent ranks

**Branch:** `s8-hybrid` (base `main` @ `a43cd574`) · **Worktree:**
`/Users/sidsharma/Memphant-s8-hybrid` · **Date:** 2026-08-01 ·
**Spend: $140.80 of a $180 ceiling** · **Not merged, not pushed.**

Build log: `docs/build-log/2026-08-01-hybrid-retrieve-then-rank.md`
(Part A preregistration, Part A2 stage-3 selection, Part B results).

---

## Two bounds, ahead of the number

1. **One attempt, pool median 124.5 units.** MemPhant's whole pool is already
   nearly the whole attempt, which is exactly why S4's `list_events` brute force
   costs $0.144/question. **Every `N*` here is `N*` at a 124-item haystack.** The
   regime where narrowing pays — repo scale, cross-session — is the regime this
   bank cannot test, and nothing is extrapolated into it.
2. **The bank withholds identifier surfaces** (q→target coverage 0.1346 vs human
   0.175–0.287), so **any margin `grep` holds here is a LOWER bound.**

## Result

n=180, endpoint `gate_common.provenance_hit@10 over top-10 bodies` — S4's exact
string — same query, nested haystacks.

| arm | hits@10 | ceiling | RankAcc | random floor | $/question |
|---|---:|---:|---:|---:|---:|
| agentic `grep` (S4, banked; raw events = a SUPERSET) | **174** | 178 | — | — | $0.144 |
| H(N=128) | 155 | 168 | .923 | 31.9 | $0.230 |
| **H(N=64)** | **154** | 164 | .939 | 48.8 | $0.191 |
| H(N=16) | 120 | 127 | .945 | 94.3 | $0.151 |
| MemPhant fused@10 (stage-matched) | 112 | 169 | — | — | **$0** |
| MemPhant packed@10 (shipped default) | 105 | 169 | — | — | **$0** |

| contrast | b | c | n_d | Δ | p | verdict |
|---|---:|---:|---:|---:|---:|---|
| **H(64) vs fused@10** | 48 | 6 | 54 | **+23.33pp** | 3.26e−09 | **H wins** |
| H(64) vs packed@10 | 55 | 6 | 61 | +27.22pp | 5.38e−11 | H wins |
| **H(64) vs agentic `grep`** | **0** | 20 | 20 | −11.11pp | 1.91e−06 | the comparator wins |

## The three things this lane found

**1. Agent ranking recovers essentially all the ranking headroom.** The fuser
turns a pool containing the gold on 164/180 into 112 hits; an agent over the same
pool returns 154. b=48 against c=6.

**2. The preregistered prediction was wrong: RankAcc does not decline with N.**
Excluding rows the pinned provider refuses outright, it is **1.000 at N=16, N=64
and N=128** — not one gold the retriever handed over was ever ranked away. The
apparent decline (.945 → .923) is entirely the refusal count rising *with
coverage*. So `hit@10(N) = Coverage(N)`, there is no interior maximum, and
**N\* = 64 is a cost knee** (where coverage flattens, 164 → 168), not an
attention limit. At 128 candidates of ~1,077 chars the agent's judgement limit
has **not** been reached; where it is was not measured.

**3. The hybrid still loses to `grep`, at higher cost.** 154 vs 174 at 1.32×
`grep`'s $/question, **b-cell 0**. On a 124-item haystack there is no reason to
narrow first.

## Gap decomposition at N=64 — 26 misses, zero of them ranking

* **16 out-of-view** — gold not in MemPhant's top 64 (11 of these are outside the
  pool at any rank: the hard §A.2 ceiling).
* **10 provider refusals** — `content_filter`, deterministic at temperature 0.
* **0 ranking failures.**

## Verdict: a product, or a dead end?

**Neither, and the third answer is the useful one.** The hybrid is not shippable
on this evidence — the cost case for narrowing cannot be made at 124 items, and
here it is beaten on both accuracy and cost by brute force. But the lane measured
something more actionable than the architecture it was testing:

> **MemPhant's recall is good and its fusion is bad.** Coverage 0.9389 — within
> 2.8pp of what `grep` realizes — converted by the shipped fuser into 0.5833.

An LLM re-ranker closes 81% of that gap, expensively. The finding that matters is
that **a perfect ranker over the pool MemPhant already retrieves would score
164/180 = 0.911 at $0**, against the shipped 105. **That bounds every cheap-ranker
investment — cross-encoder, better fusion, learned rerank — at +59 questions and
not one more**, because 164 is the pool's own ceiling at N=64. That is the number
to hand the reranker lane.

## What it does not license

Does not authorize the hybrid as an architecture. Does not move a default. Does
not measure answer accuracy, latency, or anything at repo scale. Does not rescue
S4: `grep` still wins, by 20 questions instead of 68. Artifacts are
`decisional: false` — the bank fails its own preregistered leakage bar
(concentration 2.018 vs ≤1.50) and its corpus licence is a card assertion.

## Deviations (full list in build log §B.8)

1. §A.1's central prediction falsified; Part A left unedited.
2. `--refusals-as-miss` added mid-lane — one deterministic provider error class,
   scored **against** the hybrid, every question named, refused for any other
   error kind, pinned by a test. 14 refusals here vs S4's 3 on the same bank and
   model; at S4's rate H(64) would score ~161–164. **No verdict turns on it**,
   but the gap to `grep` is overstated by roughly half.
3. Stage-3 N selection rule recorded in Part A2 *after* the sweep and *before* any
   n=180 cell, because the two raw-best N values tied and were not distinct
   mechanisms.
4. Pool dump killed and relaunched under `scripts/detach_run.py` on finding it in
   the launcher's process group before a 60-minute lifecycle boundary. No paid
   call had been made.
5. A comment wrongly attributed a `required_n` transposition to S4's compare
   script. S4 does not contain it; corrected in `dde3a949` and replaced with a
   test at this lane's own call site.

## Mechanism liveness

`pool_containment_violations = 0` on all three arms, checked **per returned
body** against the exact set that question's agent was handed; the runner exits
non-zero rather than scoring a mismatch. No `list_events`, no filesystem, no tool
that can name an event or a path. Retriever half live from its own provenance
(`bm25-code`, `small`, 21,630 events compiled); this run's packed top-10
reproduces the banked default at 105/180 vs 106/180 with the **coverage curve
reproducing exactly**.

## Spend

$70.10 sweep + $18.94/$24.26/$27.50 incremental confirmations = **$140.80** of
$180. Confirmation totals double-count carried rows by construction; the
incremental column is the ledger. Basis: pinned catalogue price × reported
tokens (upper bound). Provider pin held for every call.

## Harness left behind

* `scripts/code_lane_run_hybrid_rank.py` — the arm, with pool containment and a
  per-question checkpoint (fsynced, resumable, refuses a checkpoint from a
  different depth/model).
* `scripts/s8_hybrid_analyze.py` — sweep analysis, exact McNemar, per-N ceilings,
  random-ranker floor, generated evidence contract.
* `scripts/code_lane_run_memphant.py --dump-pool` — banks the fused pool **with
  bodies**; the banked `channel_table` has ranks and ids but no bodies, so no
  retrieve-then-rank arm could be built offline without it.
* `tests/test_s8_hybrid.py` — 21 tests pinning the endpoint contract, the caps,
  containment, ceiling arithmetic, the n_d floor, refusal handling and checkpoint
  resume.
