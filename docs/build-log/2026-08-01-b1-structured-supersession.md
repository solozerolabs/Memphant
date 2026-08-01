# B1 — the structured-state extractor: supersede by naming the unit, not the key

**Date:** 2026-08-01 · **Worktree:** `Memphant-af-b1-structured` · **Branch:** `af-b1-structured`
**Base:** `accuracy-first` @ `d01affad` · **Cost:** `paid_model_calls: 0` throughout.

> Sections 1–3 are the design and the **preregistration**. They were committed
> before the confirmatory arm ran. Sections 4 onward are the measurement, added
> on branch `w1-b1arm` off `main` @ `5d7b9d5a`.
>
> **Verdict in one line (§10):** naming a unit id makes bitemporal supersession
> fire and buys **≈16%** of the oracle headroom (§7.1 — ceiling re-measured
> 2026-08-01; this denominator drifts, do not carry it forward), but a
> rate-matched arm that reads no bodies at all captures **half** of that, and
> B1's semantic increment (+0.0247, CI [−0.0033, +0.0532]) **does not clear
> zero** on the confirmatory slice. Positive mechanism, unproven semantics —
> **as implemented**, meaning a whole-body content-word Jaccard at τ = 0.25,
> which §10.1 shows is the wrong unit and is the only selector anyone has run
> live. This is not a verdict on semantic selection in general.

## 1. The problem B1 attacks

A4 measured the binding constraint precisely. Supersession is keyed on
`fact_key`, and the write path only closes a prior unit's generation when it can
produce a key that **matches** that unit's. On MemoryCode:

- gold-independent, body-derived key production recovers **8 of 1,063 groups**
  (0.8%) — `2026-07-31-preference-writepath.md` §4;
- feeding the state machine the instrument's own gold key takes latest-state-wins
  from **0.3123 to 0.5795**, **+0.267** — the same log, §5.

So the state machine works and key production is what fails. B1 removes key
production from the supersession decision altogether.

## 2. The design, in one paragraph

`ReflectCandidate::target_unit_ids` already existed, but the write path's
targeted branch *also* required `unit.fact_key == candidate.fact_key`. Naming an
id therefore did not bypass key production at all: the caller still had to mint a
key that matched the incumbent's, which is the thing that does not work. B1 drops
that equality, so **identity is the uuid**. A candidate names the exact prior
units it replaces and the compiler closes their generations directly. The
extractor that produces those ids is handed one thing — the candidate's
**retrieval context**, a single `/v1/recall` into the scope the write is about to
land in — and names the top-ranked live unit when a content-word Jaccard against
its body clears a cutoff. The unit it mints carries a **content hash of its own
body** as a subject key: a self-identity, derived with zero knowledge of any
prior session, which by construction can never coincide with one.

### 2.1 What was kept, deliberately

Dropping key equality removes one guard, so every remaining one is load-bearing
and none were relaxed: same scope, `state = Active`, `transaction_to is null`,
**own-kind only** (RW-3, so an episodic candidate still cannot close a semantic
generation), bitemporal overlap via `candidate_targets_unit`, and trust
dominance. A named target that fails any of them fails the **whole write**, not
just the edge — an unresolvable or stale id must never be silently ignored,
because ignoring it appends a second live rule beside the one the caller meant to
retire, which is exactly the accuracy failure B1 exists to fix.

### 2.2 The trust rule, stated and enforced

**A non-empty `target_unit_ids` is a rank-0-trust capability. Anything below it
is refused, and the refusal fails the write closed.**

Naming another row's id is a directed mutation of a unit this candidate did not
author. That is categorically different from a *kind hint*, which is a claim
about the candidate itself and which RW-7 correctly degrades to a belief.
Silently degrading a supersession directive into an append is precisely the shape
that produced the `20260731_007` exclusion crash — the write lands, nothing is
closed, and a second open row hits the constraint. An untrusted extractor must be
told it was refused. `Some([])` is only a create precondition, mutates nothing,
and stays open to any caller.

Within rank 0 there is no finer ordering to appeal to: this repo's own
`trust_risk_rank` puts `TrustedUser` and `TrustedSystem` in one tier. The
dominance check (`trust_risk_rank(candidate) <= trust_risk_rank(target)`) is
therefore vacuous today and is kept anyway, because it is the invariant the tier
gate is an approximation of, and it is what stops a one-line widening of the gate
from silently handing the capability to a lower tier.

### 2.3 The exclusion constraint, guarded rather than hoped for

Named targets no longer have to be the incumbents on the candidate's own key, so
any *unnamed* open own-kind row on that key would still be open after the write
— which on the kinds `supersedes_own_kind` owns is exactly what
`memphant_memory_unit_subject_valid_excl` rejects. The create-collision check was
widened from "the target list is empty" to "any open own-kind row on this key
that the candidate did not name", conditioned on
`supersedable_kind.is_some()` — the constraint's own predicate. The write fails
with the key named rather than in the persist transaction.

The governing rule in `20260731_007`'s header is untouched: the constraint's
`kind` predicate is still exactly `{k : supersedes_own_kind(k) == Some(k)}`, and
`exclusion_predicate_matches_the_supersedes_own_kind_set` still derives both
sides.

### 2.4 Named simplifications, with their ceilings

- **One unit per SESSION.** The oracle arm mints one per *declaration*; a session
  that states several conventions cannot be split by anything this extractor can
  see. This caps B1 below the oracle's 0.5795 by construction — the residual the
  oracle arm itself attributes to session-granularity is inherited whole.
  *Upgrade path:* segment a session before keying it (spec 04 §13; the
  mutation-time hook band of 92–93% in `2026-07-31-preference-writepath.md` §7).
- **One target, top-ranked only.** Scanning further down the recall pool is a
  looser rule than the one preregistered and was not run.
- **A content-word Jaccard, not a model.** $0 forbids the extraction call. The
  cutoff is the arm's only free parameter.
- **The public key stays required.** `RetainUnitPayload.fact_key` is still
  mandatory, so B1 bypasses key *matching*, not key *presence*. A self-identity
  hash satisfies it at zero cost, so making it optional buys nothing today.

## 3. Preregistration

Committed at `9ce4fa8b`, before the confirmatory arm ran.

**Primary endpoint.** Latest-state-wins (LSW) = appropriate-application rate, the
same endpoint and the same three mutually-exclusive buckets as
`2026-08-01-preference-lane-prereg.md`. Analysis is
`scripts/preference_lane_analysis.py` unchanged: cluster bootstrap over
instances, 10,000 resamples, seed 20260801, plus exact two-sided McNemar and a
cluster permutation test.

**Arms.**

| arm | what it is | comparable to |
|---|---|---|
| **S** | B1 extractor at the dev-chosen threshold | B, A′ |
| **S0** | identical, `--structured-threshold 2.0` — supersession disabled, recall and ledger still performed | S (isolates the edge) |
| **A′** | unchanged episodic path (banked, re-verified on this tree) | B |
| **B** | BM25 lexical control, DB-free | all |
| **P** | oracle-keyed ceiling, `decisional: false` | none — reference point only |

**Split.** `--group-mod 0/4` is the **dev slice** (64 of 257 instances). The
threshold is chosen there and nowhere else. The **confirmatory** analysis is the
remaining 193 instances, and it is the number that carries the verdict. The
full-bank figure is reported as secondary because it contains the dev slice.

**Decision rule.** B1's value is the fraction of the oracle's +0.267 it closes:
`(LSW_S − LSW_A′) / 0.2672`. A cluster-bootstrap CI on ΔLSW that includes 0 is a
NEGATIVE and will be reported as one.

**Power floor.** Exact two-sided McNemar has no rejection region below **n_d = 6**
discordant pairs; `p = 1.0` below that is arithmetic, not evidence. Realized psi
and MDE are computed with `scripts/instrument_power.py`, never asserted.

**Mechanism liveness is a gate, not a footnote.** An inert arm and a neutral arm
produce the same score. Supersession edges emitted, generations actually closed,
and retired rows never recalled are all counted from the arm's own scratch
database on the bench superuser credential, before it is dropped.

## 3.1 The threshold, fixed on the dev slice before the confirmatory arm ran

Committed before Arm S was launched. Dev slice = `--group-mod 0/4`, 54 instances
/ 1,969 sessions / 253 probes, and **no confirmatory row was looked at**.

The live extractor names the top-ranked *live* preference unit MemPhant's own
recall returns; reproducing that ranking offline is not possible, so the dev
candidate is approximated by the repo's own BM25 (`code_lane_run_deterministic`,
the arm-B scorer) over the prior sessions of the same group. This is a dev-side
proxy and nothing downstream depends on it being the true ranker.

**First reading — the naive one, and it is misleading.** How often does the
top-1 prior neighbour actually restate the same convention?

| τ | sessions naming a target | shares a declaration key |
|---:|---:|---:|
| 0.00 | 1915 | 0.150 |
| 0.20 | 887 | 0.222 |
| 0.25 | 309 | 0.285 |
| 0.30 | 83 | 0.337 |

Precision never exceeds **0.34**. Read as "the extractor is wrong two times in
three", which is what an extraction-quality framing says.

**Second reading — the one that governs.** That framing is wrong for this
endpoint, and the difference matters. Supersession always points *backwards in
time*, and this instrument's distractors are *always earlier* declarations of
the same key. So retiring a semantically-unrelated earlier session is not
symmetric harm: it hurts only if that session is itself the gold (the most
recent declarer) of some *other* probe. Classifying each named target:

| τ | edges | retires a GOLD | retires a DISTRACTOR | neither | D − G |
|---:|---:|---:|---:|---:|---:|
| 0.00 | 1915 | 68 | 788 | 1059 | +720 |
| 0.20 | 887 | 41 | 491 | 355 | +450 |
| 0.25 | 309 | 16 | 204 | 89 | +188 |
| 0.30 | 83 | 6 | 61 | 16 | +55 |

Every operating point retires roughly **twelve distractors per gold**. A
"wrong" edge is usually still a *useful* edge on this metric.

**Why not τ = 0, then.** The proxy above ranks over *all* priors, including rows
the live system has already retired and can no longer return. That flatters low
thresholds precisely where it matters: at τ = 0 nearly every session closes a
generation, the live pool collapses toward one unit per group, and golds are
retired at a far higher rate than the removal-blind proxy shows. A removal-aware
proxy was started and abandoned when the machine's load average passed 90 with
four concurrent full-corpus benches on it; **that gap is a real limitation of
this calibration and is carried into §7 rather than papered over.**

**Chosen: τ = 0.25.** It is the best measured distractor-to-gold ratio (12.75:1)
at non-trivial coverage, and at 16% of sessions firing the pool contracts by
roughly a sixth rather than collapsing, which is the regime where the
removal-blind proxy is least wrong. One threshold, fixed here, run once.

---

> Sections 4 onward are the measurement. They were written after the arms ran,
> on branch `w1-b1arm` off `main` @ `5d7b9d5a`, and they change nothing in
> §1–3.1 above.

## 4. What "B1" is, stated plainly, because it has been mis-stated before

**B1 as measured here is not the 4,258-line LLM structured-state subsystem.**
`MEMPHANT_STRUCTURED_STATE` remains dark and was not touched by any arm in this
log. Nothing in §5–§10 is evidence for or against it.

What was actually measured is two things:

1. **The production enabler:** `e165b4b9`, *supersede by exact prior unit id,
   not by matching a subject key* — now on `main`. This is the change with
   value: the write path's targeted branch no longer requires
   `unit.fact_key == candidate.fact_key`, so a candidate can retire an
   incumbent by **naming its uuid**. Every other guard in §2.1 is intact.
2. **A ~90-line deterministic extractor inside the bench adapter**
   (`ingest_group_structured`): one `/v1/recall`, content-word Jaccard against
   the single top-ranked live preference unit, name it if the score clears τ.
   **Zero model calls, `paid_model_calls: 0`, $0.**

The extractor is a *probe* for the enabler, not a product. Read every number
below as "what does naming-the-unit buy, when the namer is the dumbest thing
that could possibly work".

## 5. Mechanism liveness — checked before any accuracy number was read

Both arms are the same code path on the same tree and the same served binary;
they differ only in `--structured-threshold`. Counted from each arm's own
scratch database on the bench superuser credential, before it was dropped.

| liveness counter | **S** (τ = 0.25) | **S0** (τ = 2.0, no-op) |
|---|---:|---:|
| top-ranked prior units seen | 7,890 | 7,890 |
| calibration-ledger entries | 7,890 | 7,890 |
| targets proposed | **1,091** | **0** |
| `supersedes` edges | 2,182 | 0 |
| units in state `superseded` | 1,091 | 0 |
| units with `valid_to` closed | 1,091 | 0 |
| superseded rows still transaction-open | 0 | 0 |
| subject keys with competing live rows | 0 | 0 |
| open subject-key range overlaps | 0 | 0 |
| remainders ever recalled | 0 | 0 |
| memory units total | 9,238 | 8,147 |
| reflect jobs left queued / failed | 0 / 0 | 0 / 0 |

This is the gate §3 demanded, and it passes in both directions at once.

- **S fired.** 1,091 generations were closed, and every closed row is closed
  *properly*: none is still transaction-open, none leaves a competing live row
  on its key, and not one of the 1,091 historical remainders was ever returned
  by a recall. The exclusion constraint of §2.3 was never violated, and it was
  never disabled to get there.
- **S0 is a no-op isolator, not an inert arm.** It performed the *identical*
  7,890 recalls and wrote the *identical* 7,890 ledger entries — it saw exactly
  the same candidates and scored them the same way — and then named none of
  them. So the S − S0 difference is **the supersession edge and nothing else**:
  not the recall, not the extractor, not the ingest order, not the binary.

The extractor's own candidate distribution, from the live ledger (not the §3.1
offline proxy): median Jaccard 0.194, p90 0.260, p95 0.283, max 0.521.
τ = 0.25 sits at roughly the 86th percentile and fires on **13.8%** of eligible
sessions (1,091 / 7,890). Two things follow. First, §3.1's prediction of "16% of
sessions firing" was close to right for the wrong reason — the live ranking is
not the BM25 proxy, but the operating point landed in the same regime. Second,
the adapter's **default τ = 0.35 would have fired 32 times in 7,890 (0.4%)** and
would have been very nearly the no-op arm; the preregistered 0.25 was the right
call and the default would have produced a null by construction.

## 6. The primary result — arm S against arm S0

Preregistered analysis, unchanged: `scripts/preference_lane_analysis.py`,
cluster bootstrap over instances, 10,000 resamples, seed 20260801, exact
two-sided McNemar reported beside it as the anti-conservative reference it is,
plus a cluster permutation test.

**The confirmatory slice carries the verdict** (dev slice `--group-mod 0/4`
excluded):

| slice | probes | instances | LSW **S** | LSW **S0** | ΔLSW | bootstrap CI95 | b | c | n_d | perm p | McNemar p |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| **confirmatory** (≠0/4) | 810 | 203 | **0.3679** | 0.3173 | **+0.0506** | **[+0.0269, +0.0753]** | 79 | 38 | **117** | 0.0002 | 1.9e-04 |
| full bank (secondary) | 1,063 | 257 | 0.3622 | 0.3142 | +0.0480 | [+0.0262, +0.0693] | 102 | 51 | 153 | 0.0001 | 4.6e-05 |
| dev slice (0/4) | 253 | 54 | 0.3439 | 0.3043 | +0.0395 | [−0.0083, +0.0858] | 23 | 13 | 36 | 0.163 | 1.3e-01 |

*A bookkeeping correction, recorded rather than quietly fixed:* §3 says the dev
slice is "64 of 257 instances" and the confirmatory remainder "193". The actual
`sha256(group_id) % 4` partition is **54 dev / 203 confirmatory** (253 / 810
probes), which is what §3.1 already reported and what every table here uses.
The split constant was never touched; only §3's arithmetic was wrong.

**The confirmatory CI excludes 0. By the preregistered decision rule this is a
POSITIVE.** n_d = 117 is far above the n_d ≥ 6 structural floor, and the
realized MDE at 80% power on this slice is 0.0383 (n = 810, ψ = 0.144,
`instrument_power.min_detectable_effect`) — the observed +0.0506 clears it.

The dev slice on its own does not reach significance (n_d = 36, CI spans 0).
That is expected and is not a contradiction: it is a quarter of the bank, it is
where the threshold was chosen, and it was preregistered as non-decisional. It
is reported because hiding it would be dishonest, not because it changes
anything.

Both other preregistered endpoints move consistently, on the full bank:

| endpoint | S | S0 | Δ | CI95 | n_d | perm p |
|---|---:|---:|---:|---|---:|---:|
| appropriate application (LSW) | 0.3622 | 0.3142 | +0.0480 | [+0.0262, +0.0693] | 153 | 0.0001 |
| misapplication | 0.6209 | 0.6736 | **−0.0527** | [−0.0746, −0.0304] | 156 | 0.0001 |
| neither returned | 0.0113 | 0.0066 | +0.0047 | [−0.0010, +0.0117] | 9 | 0.284 |

The win is a genuine transfer from *misapplication* to *appropriate
application* — stale rules stop outranking current ones (`stale_outranks_current`
660 vs 716) — and it is **not** bought by suppressing everything: the "neither
returned" bucket moves by less than half a point and its CI includes 0.

**The cost, stated rather than buried.** hit@k falls, 0.8071 vs 0.8429 on the
full bank. Supersession removes rows from the recallable set, so some probe
whose gold was retired by a wrong edge can no longer find it at any rank.
hit@1 moves the other way, 0.2709 vs 0.2408. That is the shape §3.1 predicted:
this instrument rewards retiring old rows in aggregate, and pays for it in
recall coverage.

## 7. Reference points, and how much of the oracle gap B1 closes

All rows below are the full 1,063-probe bank. **Only the S vs S0 row is a
same-tree, same-binary, same-pipeline-stage comparison.** The A′ and B rows are
banked artifacts from earlier worktrees that carry **no `lineage` block at all**
— the tree and binary that produced them are unrecoverable — so they are
reported as context and are *not* the decisional comparison.

| arm | what it is | LSW | hit@1 | hit@k | lineage stamped |
|---|---|---:|---:|---:|:--:|
| **S** | B1 extractor, τ = 0.25 | **0.3622** | 0.2709 | 0.8071 | ✅ this tree |
| **S0** | identical, τ = 2.0, supersession off | 0.3142 | 0.2408 | 0.8429 | ✅ this tree |
| A′ | unchanged episodic path (banked) | 0.3123 | 0.2314 | 0.7855 | ❌ |
| B | BM25 lexical control, DB-free (banked) | 0.3198 | 0.2681 | 0.9332 | ❌ |
| P | **oracle-keyed CEILING, `decisional: false`** | 0.5795 | 0.3283 | 0.8401 | ❌ |

| comparison | ΔLSW | CI95 | n_d | perm p |
|---|---:|---|---:|---:|
| S − S0 (the measurement) | +0.0480 | [+0.0262, +0.0693] | 153 | 0.0001 |
| S − A′ (cross-lineage) | +0.0499 | [+0.0233, +0.0775] | 223 | 0.0006 |
| S − B (cross-lineage, cross-stage) | +0.0423 | [+0.0138, +0.0711] | 261 | 0.0044 |

**Fraction of the oracle's +0.2672 that B1 closes**, using the preregistered
denominator exactly as written:

- confirmatory slice, S vs S0: 0.0506 / 0.2672 = **18.9%**
- full bank, S vs S0: 0.0480 / 0.2672 = **18.0%**
- full bank, S vs A′: 0.0499 / 0.2672 = 18.7%

### 7.1 The denominator is cross-tree, and it is wrong by about 4 points

**This correction is not mine and is cited, not asserted.** The concurrent
A-recency workstream (`w1-arecency`, committed `baa267fa`) re-ran the
oracle-keyed configuration on `main @ 5d7b9d5a` — the same base commit this
branch sits on, with only a docs-only commit between — and reports **LSW
0.6237**, against Arm P's banked **0.5795**. Its mechanism counts reproduce Arm
P's *exactly* (7,198 supersedes / 3,599 contradicts / 3,599 superseded), and
misapplication (0.3396 vs 0.3405) and stale-outranks-current (361 vs 362) are
unchanged. **The entire +4.4pp is retrieval coverage: hit@10 0.925 vs 0.840.**
The base got much better at surfacing the rule at all, somewhere between
`accuracy-first@d01affad` and `main@5d7b9d5a`, and no better at telling live
from retired.

So `0.2672` is a gap between two numbers measured on a *tree that is not this
one*, and every percentage above inherits ~4pp of base drift. Rule 4 of this
program says compare only at the same lineage; the preregistered decision rule
named a constant that silently violates it.

**Recomputed against an on-tree ceiling** (0.6237 oracle vs this tree's own
no-supersession floor S0 = 0.3142, headroom **0.3095**):

| quantity | preregistered denom (0.2672) | on-tree denom (0.3095) |
|---|---:|---:|
| S − S0, confirmatory | 18.9% | **16.4%** |
| S − S0, full bank | 18.0% | **15.5%** |
| …retirement alone (R3 − S0, conf.) | 9.7% | **8.4%** |
| …B1's semantics (S − R3, conf.) | 9.2% *(CI spans 0)* | **8.0%** *(CI spans 0)* |

The **on-tree column is the honest one** and §10 uses it. Nothing about the
verdict changes — the semantic increment still fails to clear zero — but the
headline shrinks from "a fifth of the headroom" to "about a sixth", and any
future citation of "18.9%" should be read as the cross-tree figure it is.

The caution generalises and is worth carrying into the register: the A-recency
session notes that had it reused the cited 0.5795 instead of re-running its own
ceiling, it would have reported −1.4pp and concluded the opposite of the truth.
**A banked ceiling is not a constant.**

**And that applies to this section's own output.** 0.6237 is a ceiling for *this
tree and this instrument*, and it is no more stable than the 0.5795 it replaces
— it moved 4.4pp in a day on retrieval coverage alone while the staleness
behaviour stayed flat. So every share in the on-tree column is written here as
**"≈16%, ceiling re-measured 2026-08-01"** rather than as a bare percentage, and
that is how it should be quoted. **If the figure is load-bearing for a decision,
re-cut the ceiling on the tree you are claiming against; do not carry this one
forward.** A ratio whose denominator drifts 4pp per day is context, not a
constant, and this paragraph exists so that a reader three weeks from now is
wrong in a way they can see rather than in the way "18.9%" was.

Arm P is a **ceiling, not a target**. The honest reading is: removing key
production from the supersession decision recovers a fifth of the headroom that
a perfect oracle key recovers, using an extractor that costs nothing and knows
nothing. Four fifths of the gap survives, and §2.4 already names where most of
it lives — one unit per *session* where the oracle mints one per *declaration*.

## 8. The ablation — is this B1's semantics, or just retiring old rows?

**Why this section exists, and why a positive result is worthless without it.**
MemoryCode's distractors are *always earlier declarations of the same key*.
Supersession always points backwards in time. So **any** policy that retires
older rows scores well on this instrument whether or not it understood a single
word of what it retired. §3.1 measured that directly: top-1 semantic precision
never exceeds 0.34 at any threshold, and yet every operating point retires
roughly twelve distractors per gold. A win over S0 therefore cannot, on its own,
distinguish "the extractor found a genuine restatement" from "the extractor
retired something old, and old things are usually distractors here". The only
instrument that separates them is an ablation that keeps the **edge rate**
identical and throws the **semantics** away.

The ablation replaces the Jaccard test with a per-session hash draw at a fixed
firing rate and names an uninformative target. **Two ablations were run, and
which one you look at changes the verdict**, so both are reported and the
weaker-looking one is the one that governs:

- **`recency`** names the *most recent live prior unit*. It was launched first
  on the assumption that "most recent" is maximally aligned with an instrument
  whose distractors are always earlier declarations. **That assumption is
  wrong, and R2's own numbers are what expose it:** the most-recent *live*
  prior is precisely the row most likely to be some probe's current gold, so
  `recency` is not a neutral retirement policy — it is a systematically
  *harmful* one. It is a floor, not a fair baseline.
- **`random`** names a uniformly drawn live prior. Its picks skew old simply
  because old rows are the bulk of the live pool, so it retires mostly
  distractors without consulting a single word of any body. **This is the fair
  semantics-free baseline**, and §8.3 is the section that decides the verdict.

If retirement alone were the whole story, a rate-matched ablation would
reproduce arm S's gain.

### 8.1 The rate-matched ablation (arm R2) — run on this tree, this binary

`--structured-ablation recency --structured-fire-rate 0.13828`, chosen to match
arm S's *measured* firing rate (1,091 proposals over 7,890 eligible sessions).
It matched almost exactly: **1,098 proposals against S's 1,091, a 0.6%
difference.** The arm is otherwise byte-identical to S — same tree
`0ecf8cb2`, same server binary `a06f3a29…`, same corpus sha256, same slice.

| liveness counter | **S** (semantic) | **R2** (rate-matched recency) |
|---|---:|---:|
| targets proposed | 1,091 | **1,098** |
| units superseded / valid-closed | 1,091 | 1,098 |
| top-ranked prior units seen | 7,890 | **0** — the semantic test is never consulted |
| superseded rows still transaction-open | 0 | 0 |
| competing live rows on a subject key | 0 | 0 |
| remainders recalled | 0 | 0 |

That `0` in the third row is the ablation working as designed: R2 retires the
same number of rows as S, by a rule that has never looked at a body.

| comparison | slice | probes | inst | Δ LSW | bootstrap CI95 | n_d | perm p | reading |
|---|---|---:|---:|---:|---|---:|---:|---|
| **R2 − S0** | confirmatory | 810 | 203 | **−0.0086** | [−0.0299, +0.0125] | 69 | 0.500 | **NULL** |
| **R2 − S0** | full bank | 1,063 | 257 | −0.0075 | [−0.0258, +0.0105] | 92 | 0.476 | **NULL** |
| **S − R2** | confirmatory | 810 | 203 | **+0.0593** | **[+0.0307, +0.0873]** | 150 | 0.0001 | **POSITIVE** |
| **S − R2** | full bank | 1,063 | 257 | +0.0555 | [+0.0296, +0.0807] | 203 | 0.0001 | POSITIVE |

Every n_d here is far above the n_d ≥ 6 floor, so the R2 − S0 null is a
**measured null**, not "not a measurement".

**Read alone, this looks like a clean vindication of B1 — and that reading is
wrong.** Taken by itself the table says "retirement buys nothing (−0.009,
null), so S's +0.051 must be semantics (+0.059 over R2)". That inference is only
valid if `recency` is a *neutral* use of the edge budget. It is not. Retiring
the most recent live prior is an actively bad policy on an instrument that asks
which declaration is current: R2 spends all 1,098 of its edges on the rows most
likely to be golds. Its null against S0 is the sum of a retirement benefit and a
gold-destruction cost, and it tells us nothing about the size of either.

**R2 is therefore a floor, not the ablation the prereg needed.** It establishes
that B1 beats the worst way to spend the same edge budget. §8.3 asks the
question that matters.

**"Recency" is two opposite policies, and the word hides the sign.** Put to the
A-recency workstream, this caution inverts — its reply is recorded here because
it is the cleanest statement of why R2 lands where it does (`w1-arecency`
§8.8, `343e1a0d`; no measured cell of theirs moved):

- **Recency-as-retirement** — arm R2 — *closes* the most recent live prior. On
  an instrument whose gold is by construction the latest declaring session, that
  is spending the edge budget on the golds themselves. It lands on a **floor**.
- **Recency-as-selection** — the A-recency control — retires nothing and instead
  *selects* `max(observed_at)` per `fact_key` at read time. `load_memorycode`
  takes `occurrences[-1]` and assigns `observed_at` in session order, so that
  read rule **computes the gold rule directly**. It lands on a **ceiling** — the
  most favourable trivial baseline this instrument admits.

Same word, opposite sign. The practical consequence for reading these two logs
together: their +3.0pp is a win over the strongest trivial alternative, and this
log's R2 is a loss against the weakest. Neither number transfers to the other's
question, and the coincidence of vocabulary is the trap.

### 8.2 The rescued, un-rate-matched recency ablation (arm R) — exploratory only

The earlier ablation run banked at `0ecf8cb2` is superseded by R2 as a
comparator and is retained for the record. Its caveats, all carried in that
commit message and repeated here so no future reader has to dig:

- It ran at the **default τ = 0.35**, not the preregistered 0.25. Harmless for
  this arm on its face — the recency branch of `ingest_group_structured` never
  reads the threshold — but it means the file is not a threshold-matched sibling.
- It carries **no `lineage` block**: the tree and the served binary that
  produced it are unrecoverable. Under this program's rule 1 it can never be
  promoted to a decisional comparator, whatever its numbers say.
- Its slice was believed to match no recorded `--group-mod`. **It does.**
  Recomputing `sha256(group_id) % 4` over its 77 group ids shows it is exactly
  `--group-mod 1/4` of the 257-instance bank — a valid quarter, but neither the
  dev slice (0/4) nor the confirmatory remainder.
- Its `fire_rate 0.096` had **no committed provenance**: no structured arm had
  been run when it was launched, so there was no measured rate to match it to.
  Arm S now settles that — the true rate is **0.1383**, so the rescued arm fired
  at **69%** of it and was never rate-matched.

Exploratory cells, all on its own 1/4 slice, all against banked cross-lineage
comparators except the last:

| comparison | Δ LSW | CI95 | n_d | perm p |
|---|---:|---|---:|---:|
| R − A′ (banked) | −0.0295 | [−0.0657, +0.0077] | 32 | 0.193 |
| R − B (banked) | −0.0037 | [−0.0714, +0.0675] | 75 | 1.000 |
| R − S0 (this tree, 1/4 slice) | −0.0037 | [−0.0519, +0.0453] | 45 | 1.000 |
| S − R (this tree, 1/4 slice) | +0.0701 | [+0.0236, +0.1192] | 55 | 0.010 |

It points the same way R2 does, from a different threshold at a lower firing
rate on a different quarter of the bank. It is corroboration, not evidence, and
the verdict in §10 does not rest on it.

### 8.3 The ablation that decides it — `random`, rate-matched (arm R3)

`--structured-ablation random --structured-fire-rate 0.13828`. Same tree
`0ecf8cb2`, same server binary `a06f3a29…`, same corpus, same slices, **same
1,098 proposals** as R2 (the firing draw is a function of the session id, so R2
and R3 fire on exactly the same sessions and differ only in *which* live row
they name). `top_ranked_prior_units_seen: 0` — no body was ever compared.

| comparison | slice | probes | inst | Δ LSW | bootstrap CI95 | n_d | perm p | reading |
|---|---|---:|---:|---:|---|---:|---:|---|
| **R3 − S0** | **confirmatory** | 810 | 203 | **+0.0259** | **[+0.0088, +0.0432]** | 59 | 0.005 | **POSITIVE** |
| R3 − S0 | full bank | 1,063 | 257 | +0.0188 | [+0.0029, +0.0353] | 78 | 0.030 | positive |
| **S − R3** | **confirmatory** | 810 | 203 | **+0.0247** | **[−0.0033, +0.0532]** | 146 | 0.095 | **NEGATIVE (CI spans 0)** |
| S − R3 | full bank | 1,063 | 257 | +0.0292 | [+0.0047, +0.0534] | 189 | 0.023 | positive |
| R3 − R2 | full bank | 1,063 | 257 | +0.0263 | [+0.0029, +0.0500] | 128 | 0.038 | random beats recency |

Every n_d is far above the floor. These are measurements, not ties.

**The two findings, stated without hedging.**

1. **Semantics-free retirement is worth about half of B1's headline gain.**
   Naming a *uniformly random* live prior — no bodies read, no threshold, no
   recall ranking consulted — recovers **+0.0259** of the **+0.0506** that arm S
   recovers over S0 on the confirmatory slice, with the CI clear of zero. §3.1's
   distractor-asymmetry reading is vindicated in exactly the form it was
   written: on this instrument, retiring old rows is *intrinsically* worth
   points, whether or not anything was understood.
2. **B1's semantic increment does not clear its own confidence interval on the
   slice that carries the verdict.** S − R3 on the confirmatory 810 probes is
   **+0.0247, CI [−0.0033, +0.0532]**. The interval includes 0. **By the
   preregistered decision rule, that is a NEGATIVE and is reported as one.**
   The full-bank figure (+0.0292, CI [+0.0047, +0.0534]) does exclude 0, but the
   full bank *contains the dev slice on which τ was chosen* and was preregistered
   as secondary. A secondary endpoint does not overturn the primary one, and
   choosing the slice after seeing both is exactly the move this program has
   already voided a headline number for.

So the decomposition of arm S's +0.0506 over S0, on the confirmatory slice:

| component | Δ LSW | CI95 | established? |
|---|---:|---|---|
| total, S − S0 | +0.0506 | [+0.0269, +0.0753] | **yes** |
| …of which retirement alone (R3 − S0) | +0.0259 | [+0.0088, +0.0432] | **yes** |
| …of which B1's semantics (S − R3) | +0.0247 | [−0.0033, +0.0532] | **no** |

As a fraction of the **on-tree** oracle headroom of +0.3095 (§7.1 — the
preregistered +0.2672 is a cross-tree constant and overstates every share by
about a sixth): the edge mechanism closes **≈16%**, of which **≈8% is
established as retirement** and **≈8% is attributed to semantics but not
established**. Ceiling re-measured 2026-08-01 and drifting; the *deltas* are the
durable quantities here, not the shares.

**Why this matters more than the raw numbers.** Without R3 this log would have
reported "B1 recovers a fifth of the oracle gap and the ablation proves it is
semantics" — an overclaim by roughly a factor of two, supported by a real
measurement (R2) that happened to be the wrong control. The ablation did its
job. It just did not produce the answer that was hoped for.

## 9. Limitations, and what would change the verdict

1. **The τ = 0.25 calibration used a removal-blind proxy** (§3.1, carried
   forward as promised). The live ledger now shows how far off that proxy was:
   the real candidate distribution is median 0.194 / p95 0.283 / max 0.521,
   nothing like the offline all-priors distribution, and the adapter default
   τ = 0.35 would have fired 0.4% of the time. τ was still fixed before the
   confirmatory arm ran and was run once, so the *preregistration* holds; but
   the number it was fixed on was measured on the wrong distribution. **A
   ledger-based re-calibration is now free** — both S and S0 banked all 7,890
   live candidate scores — and it is the cheapest open lever here.
2. **One unit per session** (§2.4), inherited whole from the oracle arm's own
   session-granularity residual. This is the single largest known cause of the
   four fifths of the oracle gap that survives.
3. **One target, top-ranked only.** Scanning further down the pool was
   preregistered as out of scope and was not run.
4. **A′ and B carry no lineage block.** Every cross-lineage row in §7 is
   context, not evidence. The only decisional comparisons in this log are
   S / S0 / R2 / R3, which share a tree, a binary sha256, and a corpus sha256.
5. **`git_dirty: true` on S, R2 and R3, and what it is.** The stamp is taken
   when the report is written. S0 finished first onto a clean tree; each later
   arm then saw the *previous arm's own untracked artifact* sitting in
   `docs/build-log/artifacts/`. No tracked file differed between the arms —
   they ran from one commit, `0ecf8cb2`, and from one server binary,
   `a06f3a29…`, which is the claim the lineage rule exists to protect. Recorded
   rather than explained away.
6. **This is not a measurement of the LLM structured-state subsystem** (§4).
   `MEMPHANT_STRUCTURED_STATE` stayed dark throughout.
7. **What S vs S0 does *not* establish** — a caveat contributed by the
   concurrent A-recency workstream and worth carrying: S vs S0 compares
   supersession-on against supersession-off *within* the bitemporal design,
   holding identity production fixed. It is consistent both with "the bitemporal
   edifice earns its keep" and with "any latest-wins rule earns its keep and the
   edifice is one expensive way to implement it". Note that S0 has zero
   competing live rows on any subject key *by construction* — with uuid identity
   and no supersession there is nothing to compete — whereas an A-recency
   control has competing rows and must choose between them at read time. That is
   a different mechanism answering a different question, and neither result can
   borrow the other's.

   **That workstream has now answered its half, and it comes out in B1's
   favour.** `w1-arecency` @ `baa267fa`, 1,063 probes / 257 instances, both arms
   oracle-keyed identically on one pair of binaries: full bitemporal **0.6237**
   against `ORDER BY observed_at DESC` **0.5936**, ΔLSW **+0.0301**, CI95
   [+0.0170, +0.0443], n_d 62, cluster-permutation p 1.0e-4; misapplication
   moves the same way, −0.0245, CI95 [−0.0376, −0.0125]. Its liveness gate
   passed two-sided (bitemporal 7,198/3,599/3,599 with 0 overlapping open pairs;
   control 0/0/0 with 10,294 overlapping open pairs and 1,060 keys carrying
   competing live rows). So with a *correct key held fixed*, the bitemporal
   state machine beats plain recency by about three points. **Cited, not
   asserted, and not merged into any B1 cell** — it is a different arm pair on a
   different branch, and it belongs in the same paragraph as this log's result
   rather than inside its tables.

## 10. Verdict

**Did the mechanism work?** Yes, unambiguously. `supersede by exact prior unit
id` (`e165b4b9`) fires on a real corpus at scale: 1,091 generations closed, zero
transaction-open leaks, zero competing live rows, zero remainders ever recalled,
the §2.3 exclusion constraint never violated and never disabled. Removing key
production from the supersession decision converts a mechanism that recovered
**0.8% of groups** into one that fires on **13.8% of sessions**. That is the
engineering result and it is solid.

**Did it pay?** Yes, but less than the headline says, and for a reason that is
half boring. Arm S beats its own no-op isolator by **+0.0506** on the
confirmatory slice, CI [+0.0269, +0.0753] — **≈16% of the oracle headroom,
ceiling re-measured 2026-08-01** (§7.1; 18.9% against the preregistered
cross-tree constant, which is stale). That denominator moved 4.4pp in a day;
re-cut it before leaning on the ratio.
About half of that is **retirement per se**: a rate-matched arm that reads no
bodies at all captures **+0.0259**, CI clear of zero.

**Do B1's semantics add anything over plain distractor retirement?**
**Not established — for the selector that was run.** The qualifier is
load-bearing and is stated before the number rather than after it: the only
semantic selector any live arm has used is a whole-body content-word Jaccard at
τ = 0.25, and §10.1 shows that unit is dominated by filler every session shares.
A negative here is a negative about *that* selector. S − R3 on the confirmatory slice is **+0.0247 with CI
[−0.0033, +0.0532]** — the interval includes zero, which the preregistration
defines as a NEGATIVE. The point estimate is positive and roughly the size of
the retirement effect, and the full-bank (secondary, dev-contaminated) figure
does exclude zero, so the honest summary is: *the semantic contribution is
plausibly real and roughly half the total, and this experiment could not
demonstrate it.* It is not a null result — n_d = 146, well above the floor, and
the MDE on this slice is ~0.038 against a point estimate of 0.025 — it is an
**underpowered positive**, and the fix is more probes, not a better story.

*Why the qualifier, rather than the bare clause.* The point is owed to the
A-recency lane (`w1-arecency` @ `bfb58631`), which took §7.1's shelf-life rule
in the harder direction — against someone else's number rather than its own.
That rule was stated for absolute *rates*: not differenced within a single run,
so it has a shelf life. The sharpening is that **an absolute claim about a
MECHANISM inherits the shelf life of the implementation it was measured on, not
only of the tree it ran on** — and that this is the easier one to miss, because
a finding arrives as prose and prose does not look like it has a denominator.
"Unproven semantics" would have been carried forward indefinitely as a fact
about semantic selection. It is a fact about one Jaccard at one τ. `as
implemented` is what makes that denominator visible, and it belongs on more
claims in this program than currently carry it.

**Should B1 be pursued?** Qualified yes, in this order, and *not* as an LLM
subsystem:

1. **Keep `e165b4b9`.** It is on `main`, it is the production enabler, and its
   value does not depend on any of the above being significant: it removes a
   guard that made targeted supersession unusable. Nothing here argues for
   reverting it.
2. **Change the similarity UNIT before touching τ** — see §10.1. This
   supersedes what this section originally recommended, on someone else's
   measurement, and the correction is the more valuable of the two.
3. **Then re-calibrate τ on the new distribution.** Free, $0, no ingest. §9.1
   stands — the threshold was chosen on a distribution that is not the live one
   — but re-calibrating *first* would optimise the diluted scale and bake it in.
4. **Then re-run S against R3 only** — S0 is now redundant as a comparator,
   since R3 dominates it as a baseline. One arm pair, and the decision is
   whether the semantic increment clears zero.
5. **Do not invest in a model-based extractor yet.** It is a paid-call bet on an
   increment that is currently +0.025 ± 0.028 over a random policy, and there
   are two $0 moves ahead of it that attack the same quantity.
6. **Session segmentation (§2.4) is where the remaining four fifths lives**, not
   in extractor quality. If anything gets a redesign budget, it is that.

### 10.1 The unit, not the threshold — a correction from the key-production lane

§9.1 and this section's original recommendation #2 both read arm S's compressed
live distribution (median 0.194, p95 0.283, max 0.521) as "τ was miscalibrated;
re-calibrate it". The key-production workstream (`w1-keyprod` @ `3fda9537`,
artifact `docs/build-log/artifacts/2026-08-01-key-production/ledger-rescored.json`,
`decisional: false`) tested a different reading **on this log's own banked
ledger**, and it is better supported.

Hold all 7,890 candidate pairs fixed — exactly the pairs arm S's ranker chose —
and change only the compared object: whole session body → best-matching
directive sentence. Precision is the fraction of fired pairs that genuinely
co-declare a convention, at a matched number of firings:

| firings | body | sentence |
|---:|---:|---:|
| 100 | 0.460 | 1.000 |
| 500 | 0.402 | 0.972 |
| **1,091** (arm S's operating point) | **0.341** | **0.765** |
| 2,000 | 0.296 | 0.556 |

**Precision at arm S's actual operating point more than doubles, 0.341 →
0.765.** The body column independently reproduces §3.1's "precision never
exceeds 0.34", which is a useful check that the two lanes are measuring the same
object.

The diagnosis: a MemoryCode session is ~2,300 characters of mentor small talk
wrapped around one directive sentence, so a whole-body Jaccard is dominated by
filler that every session shares. That is why the live distribution tops out at
0.521 and why the adapter default τ = 0.35 fires 32 times in 7,890. **The
extractor is starved by its unit, not by its threshold**, and re-calibrating τ
would optimise the diluted scale rather than fix it — which is precisely what
this section originally told the reader to do.

**Verified here rather than taken on trust**, since it rewrites this log's top
recommendation: the scorer uses no gold. `literal_sentences` is a pure heuristic
over body text (sentences carrying a quoted literal); `gold_structure` supplies
only the `co_declaring` *label*, never a score; and the `max` is taken over all
sentence pairs of the two bodies, which a live extractor can compute at runtime
without knowing which pair is right. Corpus sha256 pinned and checked, upstream
lineage names arm S's artifact and its binary sha256s, `paid_model_calls: 0`.

**Carried caveats, at that lane's request and because they are correct.** It
re-scores pairs the live ranker *already returned as top-1*, so it says nothing
about what a sentence-level scorer would **retrieve**; it is a precision
comparison at matched cost, not latest-state-wins; only a live arm can carry
LSW, and `decisional: false` is the right flag.

**One caution this log adds, from §8.3.** Doubling precision does not double the
gain, because roughly half of arm S's +0.0506 is retirement that owes nothing to
semantics. The quantity a unit swap must move is the **semantic increment over
R3** (+0.0247, CI [−0.0033, +0.0532]). The thing to watch is precision *in
excess of what a random policy achieves*, which on this ledger is the
co-declaring base rate 1,174 / 7,890 = **0.149**: body clears that baseline by
0.192, sentence by 0.616. That ordering is the reason to run the swap. **It is
not a prediction of an LSW number, and none is offered here** — the increment is
measured by re-running S against R3, or it is not measured.

**What this log establishes for the register, stated so it cannot be over-read:**
naming a unit id makes bitemporal supersession fire on a real corpus and buys
≈16% of the oracle headroom (ceiling re-measured 2026-08-01, and drifting);
roughly half of that is available to a policy that understands nothing; and the
semantic half of it has not been demonstrated at the preregistered bar **by the
one selector that has been run live — a whole-body Jaccard at τ = 0.25, which
§10.1 shows is the wrong unit.**

## 11. Reproduce

```bash
cd /Users/sidsharma/Memphant-w1-b1arm      # branch w1-b1arm @ 0ecf8cb2
docker start memphant-postgres-1
cargo build --release --bin memphant-server --bin memphant-worker --bin memphant-cli
# served binary sha256 for every arm below:
#   server a06f3a295ad0685dddf33f09721b9e8c25096df77eb7d3384f1500de2b1af3ee
#   worker c8017e7701445c9c805f48eabdbfede9c4d32a5911872ffeb0043bcb4410a926
OUT=docs/build-log/artifacts/2026-08-01-b1-structured
SRC=~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet
PY=<venv-with-pyarrow>/bin/python

# Arm S -- the treatment, at the preregistered threshold. ~91 min wall.
$PY scripts/external_instrument_adapter.py --instrument memorycode --arm structured \
  --diagnostics --source $SRC --out $OUT/arm-s-structured.json --port 39501 \
  --structured-threshold 0.25

# Arm S0 -- identical, supersession disabled. Recall + ledger still performed. ~89 min.
$PY scripts/external_instrument_adapter.py --instrument memorycode --arm structured \
  --diagnostics --source $SRC --out $OUT/arm-s0-noop.json --port 39502 \
  --structured-threshold 2.0

# Arm R2 / R3 -- rate-matched ablations at S's MEASURED rate 1091/7890 = 0.13828.
$PY scripts/external_instrument_adapter.py --instrument memorycode --arm structured \
  --diagnostics --source $SRC --out $OUT/arm-r2-ablation-recency-ratematched.json \
  --port 39503 --structured-threshold 0.25 \
  --structured-ablation recency --structured-fire-rate 0.13828
$PY scripts/external_instrument_adapter.py --instrument memorycode --arm structured \
  --diagnostics --source $SRC --out $OUT/arm-r3-ablation-random.json \
  --port 39504 --structured-threshold 0.25 \
  --structured-ablation random --structured-fire-rate 0.13828

# Preregistered analysis, unchanged, for any arm pair on the SAME probe bank:
$PY scripts/preference_lane_analysis.py --arm-a $OUT/arm-s-structured.json \
  --arm-b $OUT/arm-s0-noop.json --out $OUT/analysis-s-vs-s0.json
```

Slice restriction is a filter on `rows`, applied identically to both arms before
the analysis script sees them — the script refuses arms whose probe banks differ:

```python
h = lambda g: int(hashlib.sha256(g.encode()).hexdigest(), 16) % 4
confirmatory = [r for r in report["rows"] if h(r["group_id"]) != 0]   # 810 probes / 203 instances
dev          = [r for r in report["rows"] if h(r["group_id"]) == 0]   # 253 probes /  54 instances
```

`pyarrow` is the only extra dependency. Every arm verifies the pinned corpus
sha256 `1edb1238…` before a database is minted, self-re-execs onto an ephemeral
scratch DB, and drops it on exit. `paid_model_calls: 0` on every arm.

## 12. Artifacts

| artifact | contents | lineage |
|---|---|:--:|
| `artifacts/2026-08-01-b1-structured/arm-s-structured.json` | **Arm S**, τ = 0.25, 1,063 rows, diagnostics, 7,890-entry calibration ledger | ✅ |
| `artifacts/2026-08-01-b1-structured/arm-s0-noop.json` | **Arm S0**, τ = 2.0 no-op isolator, 1,063 rows, identical 7,890-entry ledger | ✅ |
| `artifacts/2026-08-01-b1-structured/arm-r2-ablation-recency-ratematched.json` | **Arm R2**, rate-matched recency ablation, 1,098 proposals | ✅ |
| `artifacts/2026-08-01-b1-structured/arm-r3-ablation-random.json` | **Arm R3**, rate-matched random ablation, 1,098 proposals — the decisive control | ✅ |
| `artifacts/2026-08-01-b1-structured/arm-r-ablation-recency.json` | rescued un-rate-matched ablation, 271 rows, §8.2 caveats | ❌ |
| `artifacts/2026-08-01-b1-structured/arm-b-lexical.json` | Arm B, BM25 control, 1,063 rows | ❌ |
| `artifacts/2026-08-01-preference-lane/arm-a-memphant.json` | Arm A′, banked | ❌ |
| `artifacts/2026-07-31-preference-writepath/arm-p-preference.json` | Arm P, oracle ceiling, `decisional: false` | ❌ |

| commit | what |
|---|---|
| `9ce4fa8b` | prereg (§1–3) + the arm, committed before any confirmatory run |
| `e165b4b9` | **the production enabler** — supersede by exact prior unit id |
| `dcc6b1a9` | the ablation arm |
| `0ecf8cb2` | the rescued ablation banked with its caveats |

Every cell in §6–§8 is reproducible from
`artifacts/2026-08-01-b1-structured/analysis/*.json`, one file per arm pair,
each carrying its own `evidence_contract` with `n`, `b`, `c`, `n_d`,
`psi_observed` and an `mde_at_80` recomputed by
`instrument_power.min_detectable_effect` rather than asserted. The `*_q1.json`
files are the §8.2 exploratory cells on the rescued arm's 1/4 slice.
