# S6 — the MemoryCode as-of re-cut

**Source branch `s6-asof` · 2026-08-01 · $0, no paid model call on any path · locally merged into
integration `main` at `1358088b`; not pushed.**
Build log: `docs/build-log/2026-08-01-asof-recut.md`.
Artifacts: `docs/build-log/artifacts/2026-08-01-asof-recut/` (10 arm reports, 4
paired analyses, all four analyses registered as `contracted`).

---

## The answer to the question S6 was given

**Do the bitemporal +3.01pp and Arm K's +0.0583 survive a corpus that does not
flatter recency? No. Neither survives, and the reason is more useful than either
number.**

**At n = 3,599 probes over 257 instances, on a cut where `max(observed_at)` is
provably wrong on every probe (it measures exactly 0.000000): the full
bitemporal supersession machinery scores 0.7399 latest-state-wins against
0.9064 for `max(observed_at ≤ t)` — a ~20-line read rule with no substrate, no
supersession, no valid-time columns and no database. Δ = −0.1664, cluster CI95
[−0.1902, −0.1435], 6.3× the computed MDE even at design effect 8.**

The trivial rule's misapplication is **exactly 0.000000**, with **c = 0**
discordant pairs against the winning arm — not one probe in 3,599 where it ranks
a retired rule above the live one. It does not tie the substrate; it beats it.

| banked claim | on the as-of cut | verdict |
|---|---|---|
| bitemporal − A-recency **+0.0301** | **+0.0117** [+0.0053, +0.0182] | shrinks to ~1pp, and the whole contest sits 16.6pp **below** an unrun baseline |
| Arm K − A′ **+0.0583** | +0.3531 — but A′ is **inert** (0 edges), so it is keyed-vs-unkeyed, not key quality. Against the honest baseline **K − trivial = −0.3543** | does not survive as the same comparison |

## Significant and negligible — both words are required

**P − P⁻ = +0.0117 LSW, cluster CI95 [+0.0053, +0.0182], exact McNemar
p = 1.2 × 10⁻³, n_d = 162** (far above the n_d ≥ 6 floor). The CI excludes zero,
so by the preregistered rule this is a **positive**.

It is also **substantively negligible**: 1.17pp against a **computed MDE of
1.01pp flat**, clearing significance by a hair, and **below the 2.87pp MDE at
design effect 8** — the regime this instrument actually lives in, since probes
nest at mean 14.0 per instance. On **hit@1 the same comparison is null**:
+0.0031, CI **[−0.0053, +0.0116]**, spanning zero.

Reporting the p-value without the magnitude, or the magnitude without the
DEFF-8 MDE, is reporting half of it.

## Mechanism liveness — the gate passed, and one row inverted

| | A′ | **P** | P⁻ | **K** |
|---|---:|---:|---:|---:|
| `supersedes` edges | **0** | 7,198 | **0** | 4,162 |
| valid-closed rows | 0 | 3,599 | **0** | 2,081 |
| overlapping open pairs on a key | 0 | **0** | **10,294** | **0** |
| **`remainders_recalled`** | 0 | **9,617** | 0 | **5,657** |

**`remainders_recalled` is the first non-zero in this program's history**
(every prior lane: 0). Bounded supersession tiles the valid axis, mints the
historical rectangle `[obs(j_r), obs(j_{r+1}))` carrying the prior body, and
returns it as the as-of answer. **This is the only instrument in the repo that
exercises valid-time at all.** Zero edges on A′ means it is **inert** — which is
exactly why K − A′ must not be quoted as its banked ancestor.

## Two corrections to S2's spec, both measured before adoption

1. **S2 §4.1's `valid_at = observed_at(j_r) + ε` is degenerate.** Under it the
   newest session at or before t is the gold on **3,599 / 3,599** probes — the
   re-cut would reproduce the identification it exists to break. Midpoint policy
   adopted; that collapses to **667 / 3,599 (18.5%)**.
2. **3,599 as-of probes, not 3,608.** A probe is degenerate whenever **any**
   consecutive declaration pair shares a session index, not only the final one.

## The prereg miss, recorded as a miss

§5 predicted P⁻ near 0.0000. It measured **0.7283**. `store.rs:2298` applies
`valid_from <= valid_at` **unconditionally** — not gated on
`MEMPHANT_A_RECENCY_CONTROL` — so bounded ingest hands the control as-of
truncation for free. P⁻ is therefore **A-recency + as-of truncation realized in
the substrate**, i.e. S2 §4.2's "arm 2" sharing an identical retrieval stack
with P. A better pairing than the one designed; the prediction was still wrong.

## What the substrate does win, reported because it is real

**P beats every trivial rule on hit@1**: 0.4718 vs 0.4121 (`bm25_asof`) and vs
0.1853 (`asof_truncation`, **+0.2865** [+0.2628, +0.3115]). The truncation rule
gets gold-before-stale right by construction while burying the gold below noise;
P ranks by relevance within what is valid at t. **This does not rescue the
verdict** — losing the primary endpoint by 16.6pp to a rule you could write in
an afternoon does not earn an edifice that costs a breaking migration to remove.

## Ranking, not retrieval — the fourth independent instance today

Arm P: **hit@k 0.9475, LSW 0.7399 — a 21.1pp gap that is a ranking loss, not a
retrieval loss.** P's pool is *better* than the trivial rule's (0.9475 vs
0.9064) and its answer is *worse* (0.7399 vs 0.9064). **We retrieve the right
thing and then rank it away.** S4 (93.9% in pool → 62.8% at rank ≤10) and S8
(perfect ranker 0.911 at $0) say the same from different directions.

## The conclusion — preregistered in §6b, confirmed harder than predicted

> **A corpus whose gold is computable from the fact statements themselves will
> always be saturated by a short rule.**

MemoryCode's gold is a function of the declaration sequence — latest on the
original cut, latest-before-t on the as-of cut — so a ~20-line rule computes it
either way (0.5936 then, **0.9064** now). Re-cutting changed *which* short rule
wins; it did not make the gold uncomputable from the statements. §6b predicted a
small-or-null gap; the measured gap is **−16.6pp against the substrate**.

**S7 closes it from the opposite direction:** MDN browser-compat-data is CC0,
has **705 genuine re-assertion arcs** — the regime MemoryCode structurally lacks
— and a ~20-line `scoped_interval` rule still scores **1.0000 on every band**.
**Re-assertion and non-recency currency are not the missing ingredients.**

**The falsification condition, as the specification any future instrument spend
must satisfy BEFORE purchase:** the gold must depend on evidence **outside the
statement set** — execution, external authority, or scope the statements do not
carry — such that no rule over the statements alone recovers it.

## What must not be concluded

1. **Not "supersession regressed."** The level drop was preregistered; levels sit
   on different cuts and are not comparable. Only Δ is.
2. **Not "the edifice is worthless."** ~1pp over its own control, and a hit@1
   win. What is established is that it does not earn its exit price *here*.
3. **Not "MemPhant loses to BM25."** `asof_truncation` is a time filter over a
   ~32-session bound haystack with no notion of relevance (hit@1 0.1853). **The
   finding is about the INSTRUMENT, not the product.**
4. **Not a scale claim.** `a-recency` §7 binds unchanged: at ~32–42 units/scope
   no recall family truncates.

## Keep or drop the instrument

**Keep it, do not decide on it.** It is the only thing in the repo that fires
valid-time end-to-end. But §3.2 is structural: the primary endpoint is
saturated, and no amount of probes or power changes that.

## Cost

**$0.** `paid_model_calls: 0` in all ten reports. **No paid arm is requested.**
No default, checkbox, cutover or SOTA claim moves.

## Harness debt paid

- Two chains lost to lifecycle SIGTERM (`rc=143`). Second loss instructive: a
  hand-rolled double-fork left the chain at `ppid=1` but in the **launching
  shell's process group** — **`pgid == pid` is the load-bearing assertion, not
  `ppid == 1`.** Now launched via trunk's `scripts/detach_run.py` (`f1f1ccfd`),
  verified `99128 1 99128`; 4 tests pass.
- Took trunk's `scripts/with_scratch_db.sh` (releases the host-wide bootstrap
  lock after migration rather than at EXIT).
- Added `hit_at_1` to `preference_lane_analysis.py` endpoints (additive).
