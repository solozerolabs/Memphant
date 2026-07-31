# B1 — the structured-state extractor: supersede by naming the unit, not the key

**Date:** 2026-08-01 · **Worktree:** `Memphant-af-b1-structured` · **Branch:** `af-b1-structured`
**Base:** `accuracy-first` @ `d01affad` · **Cost:** `paid_model_calls: 0` throughout.

> Sections 1–3 are the design and the **preregistration**. They were committed
> before the confirmatory arm ran. Sections 4 onward are the measurement.

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
