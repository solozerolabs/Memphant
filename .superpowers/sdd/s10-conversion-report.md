# S10 — endpoint validity: does `hit@k` predict answer correctness?

**Branch:** `s10-conversion` (base `main` @ `a43cd574`) · **Date:** 2026-08-01
**Build log:** `docs/build-log/2026-08-01-endpoint-validity-conversion.md`
**Artifacts:** `docs/build-log/artifacts/s10-conversion/s10-full180-{answer_correct,correct}.json`
**Settled spend:** $27.05 headline + ~$9 pilots/re-run, against a $150 ceiling.
**Not merged, not pushed.**

## Verdict

**`hit@k` is VALID BUT CONSERVATIVE. It is not a broken endpoint.** The
hypothesis this lane was commissioned to test is refused by the data. No
retrieval result in this program is invalidated.

P(correct | gold retrieved) = **1.0000** [0.9650, 1.0000] against
P(correct | gold NOT retrieved) = **0.4459** [0.3382, 0.5591] on the MemPhant
arm — a separation of 0.554 at **p = 2.76e-18**. On the agentic-grep arm,
0.9598 against 0.3333 at p = 3.01e-10. These are not close.

What `hit@k` does wrong is **understate**. MemPhant's hit@10 of 0.5889
corresponds to answer accuracy of **0.7722** — an 18.3-point gap, because 33 of
its 74 gold-misses are answered correctly anyway. The gold span is *sufficient*
(b = 0) but not *necessary*. **Every coding-lane `hit@k` figure is a floor on
the behaviour that matters, not an estimate of it.**

## The joint distribution — the deliverable

| arm | a (hit,✓) | b (hit,✗) | c (miss,✓) | d (miss,✗) | P(✓\|hit) | P(✓\|miss) | phi |
|---|---:|---:|---:|---:|---:|---:|---:|
| `memphant` | 106 | **0** | **33** | 41 | 1.0000 | 0.4459 | 0.650 |
| `agenticgrep` | 167 | 7 | 2 | 4 | 0.9598 | 0.3333 | 0.469 |

Arm accuracy: `memphant` 0.7722 · `agenticgrep` 0.9389 · `nomemory` 0.1278.
**All are LOWER BOUNDS** — a provider refusal scores as a non-answer, so
`agenticgrep` +3 and `nomemory` +53 rows are guaranteed-incorrect for a reason
that is not the pack. `memphant` has no refusals, so the bound runs against our
own arm's apparent advantage.

Cell `c` is bounded, not partitioned: the gold answer STRING was absent from the
pack on **at least 33 of 33**, so on this bank the "same fact, different span"
bucket is empty. Those answers came from inference over the pack or from priors,
and the no-memory arm sizes the priors channel at 0.1278.

## Does the 37.8pp retrieval win convert? Yes, at 44%

hit@10 gap **+0.3778** → answer-accuracy gap **+0.1667** →
**conversion ratio 0.441 [0.286, 0.587]** (paired bootstrap over question ids,
20,000 draws, seed 20260801, 0 undefined draws). The CI excludes 1.0
comfortably: **55.9% of this retrieval gap does not arrive at the outcome.**

Primary contrast `memphant` vs `agenticgrep`: b = 8, c = 38, n_d = 46,
delta = −0.1667, exact McNemar **p = 9.25e-06**, achieved power 0.996,
required n 75.

**Arm-pair-specific — do not generalize.** The arms differ 4.4x in mean packed
tokens (2177.5 vs 495.5) with zero rows changed by equalization on either, so
that spread is the arms' own behaviour. Extrapolating to a sweep of one
retriever's variants at similar pack sizes is unsupported.

## The Phase-3 anomaly closes, undramatically

Phase 3 saw BM25 answering at 0.4667 against hit@10 0.2556 and flagged it as the
biggest unresolved finding in the repo. It is not a broken proxy — it is
`P(correct | gold not retrieved) ≈ 0.45` doing exactly what these cells predict.
An arm at hit@10 0.2556 whose misses are answerable at ~0.45 lands near
0.26 + 0.74x0.45 ≈ 0.59 in expectation, the same order as the 0.4667 seen on 30
rows. The constant was unmeasured; it is now measured. The closing is boring,
which is worth saying so a quiet resolution is not mistaken for an unfinished one.

## One asymmetry, deliberately under-claimed

`agenticgrep` retrieves the gold and still answers wrong 7 times; `memphant`
does so 0 times. Fisher exact 0/106 vs 7/174 gives **p = 0.0471** — over the
line by a hair, **not preregistered**, so hypothesis-generating only. It is
**not** distractor density: the arm with the 4.4x *larger* pack is the one with
zero such failures. The live hypothesis is that a grep "hit" and a MemPhant
"hit" are not the same object — grep returns 6000-char excerpts, so the
adjudicated span can be present while the context making it answerable is
clipped. **If that holds, `hit@k` is not commensurable across retrieval
mechanisms**, which would matter for every cross-arm comparison this program
makes. Untested here.

## What this changes

1. `hit@k` stays. Valid, conservative, directionally correct.
2. Quote it as a **floor**. Reporting retrieval numbers as outcome numbers
   understates our own system by ~18 points on this bank.
3. Price retrieval points at ~0.44 of an answer point — and measure your own
   conversion rather than borrowing this one.
4. **The reader is not the bottleneck.** b = 0 over 106 chances: reader-side work
   has no headroom on this bank; the headroom is all in retrieval.
5. Open: cross-mechanism `hit@k` commensurability.

## Instrument work this lane paid for

Reader errors driven to **zero on both comparator arms** before any figure was
read. Three defects found and fixed with their evidence, each of which would
have manufactured a fake reader deficit: `empty_content` unattributed (it was
`length` — reasoning tokens consuming a 1024 budget), deterministic
`content_filter` refusals retried four times and charged as instrument failures,
and schema-violating `{}` replies dying at the parser where the row is already
lost. Also fixed: `code_lane_reader_compare.py` wrote prose into four
checker-validated enum fields, so **every artifact it had ever produced failed
`check_evidence_contract.py`**.

Shared plumbing was split out to land on trunk separately —
**`plumbing-detach-run` @ `f1f1ccfd`**: `scripts/detach_run.py` (macOS has no
`setsid(1)`; `nohup` leaves the process group intact, so a lifecycle SIGTERM
still lands), four enforced tests including a reproduction of the real failure,
and `RESUME=1` on the shared reader driver. Two sibling lanes have taken it.

A follow-on hazard found the expensive way and fixed on this branch
(`9bf08a7b`): RESUME must not regenerate the stage manifest. That file's lineage
block carries `git_head`/`git_dirty`, and the minted no-memory arm uses it *as*
its retrieval report, so regenerating it changes the authorization hash and the
ledger refuses to reopen. Two of three arms resumed perfectly — which is what
made the cause non-obvious.

Bounds on everything above: one over-corrected paraphrase bank (coverage 0.1346,
*below* the human 0.175–0.287 range), a one-attempt haystack (~122 items),
n = 180 with no more bank in existence, and a stage manifest carrying
`git_dirty: true`.
