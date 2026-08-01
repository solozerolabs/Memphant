# A-recency — the control the bitemporal edifice has never been measured against

**Date:** 2026-08-01 · **Worktree:** `/Users/sidsharma/Memphant-af-arecency` ·
**Branch:** `af-arecency`, based on `accuracy-first` @ `d01affad`
**Cost:** $0. No paid model call on any path. Deterministic regex-derived gold only.

> **§1–§4 are the PREREGISTRATION and were committed before either arm ran.**
> §5 onward is the result. Nothing in §1–§4 was edited after a number existed.

## 1. The question

MemPhant carries a bitemporal edifice: transaction-time vs valid-time,
`tstzrange(valid_from, valid_to, '[)')` under a GiST exclusion constraint,
supersession closing generations, remainder rectangles that tile rather than
overlap, `subject_generation`, `fact_key`, trust-gated promotion. It is the
single largest source of complexity in the substrate and it has already caused
one production-blocking defect (`memphant_memory_unit_subject_valid_excl`
rejecting every served ingest under `MEMPHANT_FACT_EXTRACTION=1`, fixed by
`20260731_007`).

It has never been measured against a trivial control.

**A-recency** is that control: *the most recent assertion about a subject wins.*
No supersession, no generations, no valid time, no trust promotion.

## 2. The control, and exactly what it bypasses

`MEMPHANT_A_RECENCY_CONTROL=1`, read once per process and cached
(`crates/memphant-core/src/lib.rs`, `a_recency_control_enabled`). Two call
sites, deliberately no more. No strategy trait, no config subsystem, no
abstraction layer.

**Write side** — `supersedes_own_kind` returns `None` for every arm. The
condition at the supersede branch is `explicit_subject && (supersedable_kind.is_some()
|| target_unit_ids.is_some())`, so with `None` returned and no caller-directed
targets the branch is never entered and every candidate appends. Consequently
*nothing* is ever written that the edifice would write:

| the edifice writes | under A-recency |
|---|---|
| `UnitState::Superseded` on the prior generation | never set — every row stays `Active` |
| `transaction_to` on the prior generation | never set |
| `valid_from = now` / `valid_to` on the closed generation | never set — every range is `(-inf, inf)` |
| remainder rectangles (`correction_rectangles`) | never minted |
| `Supersedes` / `Contradicts` edges | never minted |
| at-most-one-open-row-per-subject-key | not maintained |

**Read side** — `retain_most_recent_per_subject` collapses the fused recall pool
to one candidate per `fact_key`, chosen by `observed_at` (ties on body, matching
the fusion sort's own tie-break; rows with no `fact_key` are untouched). It
consults neither `state`, nor `valid_from`/`valid_to`, nor `transaction_to`, nor
`subject_generation`. `observed_at` is carried on every `StoredMemoryUnit`
independently of the bitemporal columns, so the control needs nothing the
edifice provides.

**The database also drops `memphant_memory_unit_subject_valid_excl`** in the
control arm. This is not a loosening — that constraint asserts the invariant
*supersession itself maintains*, so with supersession off the second assertion
on a key would be rejected at insert. The constraint is part of the machinery
under test. Scratch DB only.

*This is itself a finding worth recording before any score: **you cannot simply
not use the edifice.** The schema refuses. The complexity is load-bearing at the
DDL level, not merely at the code level.*

## 3. Instrument choice, and its limits

**Chosen: the MemoryCode preference lane, oracle-keyed (Arm P), 1063 probes over
257 instances.** `CohereLabsCommunity/memorycode` rev `32d888b1…`, Apache-2.0,
sha256 `1edb1238…`. Primary endpoint **latest-state-wins** (`appropriate_application`):
when a coding convention has been superseded, does the *current* session outrank
the retired one?

**Why this one.** The comparison has to run on a lane where supersession is the
point *and where the edifice can actually express itself.* Three candidates were
considered:

- **Arm A′ (default ingest), rejected.** Measured 2026-07-31: `memory_edge` empty,
  8147/8147 units `episodic` and `active`, zero predicates. The edifice never
  fires because nothing produces a subject key. Comparing an inert edifice to a
  control measures nothing — and a lane where the edifice cannot express itself
  is a rigged test in its disfavour, which is exactly as worthless as the reverse.
- **LME-S knowledge-update / temporal-reasoning, rejected.** `temporal_score`
  requires the literal token `current`/`latest`/`now` *and* a Semantic+Active
  unit; the lane has no subject-keyed write path at all. Same inertness problem,
  with a weaker gold.
- **Arm P (oracle-keyed), chosen.** Measured 2026-07-31: **7198 `supersedes` +
  3599 `contradicts` edges, 3599 superseded units, 0 with an open transaction, 0
  remainders ever recalled, LSW 0.5795.** This is the only regime in the repo
  where the full bitemporal machinery demonstrably runs end-to-end through
  Postgres at scale. If the edifice is worth anything, it is worth something
  here.

**What this instrument can decide.** Given a *correct* subject key handed
identically to both arms, whether the bitemporal state machine beats
`ORDER BY observed_at DESC` at telling a live rule from a retired one, at
n=1063 probes / 257 instances.

**What it cannot decide.** (a) Nothing about MemPhant vs a lexical baseline —
the key is derived from the same `topic` field the gold labels are, so both arms
are handed the grouping and neither number is comparable to Arm B. (b) Nothing
about lanes where valid-time is genuinely *bounded* — MemoryCode assertions are
unbounded "current convention" statements, so the valid axis is degenerate here
and `correction_rectangles`' tiling has nothing to tile. A win for A-recency on
this instrument therefore does **not** prove the edifice is worthless for
bounded-interval facts; it proves it is not earning its keep on the
unbounded-supersession shape, which is the shape our #1 user pain actually has.
(c) Retrieval-level only. No reader, no judge — which is what makes it $0.

## 4. Preregistered analysis and decision rule

Verbatim `scripts/preference_lane_analysis.py`, the same script and seed
(`20260801`, 10,000 resamples) the lane's prior three arms used. Arm A = full
bitemporal, Arm B = A-recency.

- Primary: **paired exact two-sided McNemar** on `appropriate_application`, with
  b, c and the per-item vectors preserved in the artifact.
- Cluster bootstrap over the 257 instances is reported alongside, because probes
  nest (mean 4.1/instance) and the McNemar is anti-conservative here.
- **Realized** psi from this run's own cells; MDE at 80% via
  `scripts/instrument_power.py`. No assumed psi.
- **The n_d >= 6 structural floor.** Exact two-sided McNemar has no rejection
  region below six discordant pairs, so `p = 1.0` there is arithmetic, not
  evidence. If this run lands below it the verdict is written as **"no
  measurement"** plus the n required — not as a tie.

**Mechanism liveness is a gate, not a footnote.** Both arms must prove which one
they are, from the database, before any score is read:

| check | bitemporal arm | A-recency arm |
|---|---|---|
| `supersedes` edges | > 0 | **0** |
| `superseded` units | > 0 | **0** |
| valid-closed rows | > 0 | **0** |
| **open rows sharing a subject key with OVERLAPPING valid ranges** (self-join) | **0** | **> 0** |
| subject keys carrying more than one live row | 0 | **> 0** |

The overlap check is a self-join, deliberately **not** an open-row count. The
bitemporal model correctly leaves the historical remainder open beside the
current generation — remainders *tile*, they do not overlap — so an
`open_semantic <= 1` assertion is the wrong test and would pass for the wrong
reason on a broken tree. The self-join computes exactly the predicate of
`memphant_memory_unit_subject_valid_excl` instead of trusting it.

Lineage (git head, branch, dirty flag, sha256 of the served server and worker
binaries) is stamped into every arm report, because lineage drift across ~19
worktrees is this program's dominant failure mode.

---

## 5. A finding that landed before any score: the schema will not let you opt out

Building the control surfaced something the score cannot, and it is worth
stating separately because it is a *structural* fact rather than a measured one.

The moment `supersedes_own_kind` stops closing generations, the second assertion
on a subject key is **rejected at insert** by
`memphant_memory_unit_subject_valid_excl`. Two live `preference` rows sharing a
`fact_key` both carry `tstzrange(null, null, '[)')`, which overlaps itself, and
the GiST exclusion constraint refuses the write. The control arm cannot run at
all until that constraint is dropped.

That is the same failure shape as the production-blocking defect
`20260731_007` was written to fix — an invariant the schema asserted while the
router had no way to keep it. Here it is the mirror image: an invariant the
router keeps and the schema *depends on it* keeping.

**What this proves.** The bitemporal machinery is load-bearing at the DDL level,
not merely in application code. Supersession is not a feature layered on top of
the store that can be switched off behind a flag; the store's own integrity
constraints are written in terms of it. Any estimate of "what would it cost to
remove the edifice" that counts only Rust lines is wrong by at least one
breaking migration.

**What this does NOT prove, and the distinction matters.** It shows that *this
schema* requires supersession. It does not show that *the problem* requires it.
The constraint exists to enforce at-most-one-open-generation-per-subject-key —
an invariant that only means anything if you have generations. A design that
never had the constraint would never need to close a generation either: an
append-only assertion log with `(fact_key, observed_at)` indexed has no
uniqueness invariant to violate, because "which one is current" is a *read-time*
question there rather than a write-time one. So the honest framing is
**removal has a migration cost, not that removal is impossible** — one breaking
migration dropping the constraint, plus whatever else keys off it
(`exclusion_predicate_matches_the_supersedes_own_kind_set` in
`crates/memphant-store-postgres/tests/fact_extraction_subject_key_pg.rs` derives
the Rust side from the SQL side and would need to go with it).

The measurement below is what decides whether that cost is worth paying. This
section only prices the exit.

## 6. A base defect fixed in passing — other lanes are hitting this wall

**`scripts/gate_runtime.py:drain_worker` rejects the current worker's output on
`accuracy-first`.** `crates/memphant-worker/src/main.rs:124` prints

```
memphant-worker: drain completed=6 failed=0 retried=0 deferred=0
```

and the parser was `re.fullmatch(r"memphant-worker: drain completed=(0|[1-9]\d*)\n?", …)`.
Every harness on the drain path — not just this one — raises
`worker drain completion output is malformed` before it reaches a single probe.
Both arms of this measurement died on it on the first attempt.

Fixed here: the richer fields are parsed, and the `failed` count is **asserted**
rather than discarded. Printing failure counts and then throwing them away was
the same defect one layer up — the worker went to the trouble of making "drained
nothing" distinguishable from "failed everything", and the harness collapsed the
distinction again.

**Agents on sibling branches whose lane touches `drain_worker` are hitting this
and should take the fix rather than rediscover it.** It is four lines.

## 7. The one confound in the pairing, measured rather than waved away

The two arms do not prune in the same place, and this is worth naming before any
number is read because it runs *against* the control.

`fetch_recall_candidates` (`crates/memphant-store-postgres/src/store.rs:2275`)
filters `transaction_to` and `valid_to` **in SQL**:

```sql
and coalesce(transaction_from, '-infinity'::timestamptz) <= $8::timestamptz
and $8::timestamptz < coalesce(transaction_to, 'infinity'::timestamptz)
and coalesce(valid_from,  '-infinity'::timestamptz) <= $9::timestamptz
and $9::timestamptz < coalesce(valid_to,  'infinity'::timestamptz)
```

So the **bitemporal arm never loads a retired row at all** — its candidate pool
is spent entirely on live rows. The **A-recency arm loads every row** (all of
them open, all unbounded) and prunes only after fusion, in
`retain_most_recent_per_subject`. If a per-family cap bound, the control would
spend pool slots on rows it is about to discard and could lose a live row that
the bitemporal arm would have kept. That would be an artifact of *where the
prune sits*, not of the recency rule.

**The caps do not bind at this corpus's scale.** They are FTS top-200,
most-recent-100 per scope, exact-subject top-200, then `truncate(limit.min(1000))`.
This corpus is 8147 units over 257 scopes (~32 units/scope on the episodic path),
and the oracle-keyed write mints 10,694 preference units over the same 257 scopes
(~42/scope). Both are far below the tightest cap of 100. No family truncates, so
the control's pool contains everything the bitemporal arm's pool contains plus
the retired rows it then drops.

**This caveat is scale-conditional, and that bounds where the finding
transfers.** At ~32–42 units per scope the caps are slack. A production scope
carrying 200+ units per subject would push the most-recent-100 family into
truncation, and there the asymmetry becomes real: the control would be
genuinely handicapped by pruning after the pool rather than inside the query,
and this result would **not** carry. Anyone citing the number at production
scope must either re-measure or move the collapse into the store query. The
finding below is about the *rule*, at a scale where the rule is what is being
compared.

---

## 8. Results — the edifice earns its keep on this instrument, by about three points

**Both arms ran on `w1-arecency` @ `5d7b9d5a`, from the same two binaries**
(server `3822e4f9…`, worker `cf985707…`, sha256 stamped into both artifacts).
No paid model call on either path. `paid_model_calls: 0` in both.

### 8.1 Mechanism liveness — read from each arm's own scratch DB, before any score

| check | prereg expects (bitemporal / control) | **measured** (bitemporal / control) | |
|---|---|---|---|
| `supersedes` edges | > 0 / **0** | **7198** / **0** | PASS |
| `superseded` units | > 0 / **0** | **3599** / **0** | PASS |
| valid-closed rows | > 0 / **0** | **3599** / **0** | PASS |
| open rows sharing a subject key with **overlapping** valid ranges (self-join) | **0** / > 0 | **0** / **10294** | PASS |
| subject keys carrying more than one live row | 0 / > 0 | **1060** / **1060** | see §8.2 |

`superseded_with_open_transaction` is 0 in both arms and `remainders_recalled`
is 0 in both, so no retired row and no remainder ever reached a recall result.
The compilation gate is clean on both sides: 3394/3394 episodes compiled, 0
failed, 0 pending, 0 dead.

**The gate passes.** Each arm proved from the database which one it is, in both
directions: the edifice demonstrably fired in arm A and was demonstrably absent —
not merely unexercised — in arm B, where 10294 pairs of open rows genuinely
overlap on a subject key and the read side therefore had to choose between them.

### 8.2 The prereg's fifth gate row is wrong, and the measurement is what shows it

The table in §4 expects **0** subject keys with more than one live row on the
bitemporal arm. Measured: **1060**, the same as the control.

This is a defect in the preregistration, not in the run, and §4's own adjacent
paragraph is where the contradiction lives: *"the bitemporal model correctly
leaves the historical remainder open beside the current generation — remainders
**tile**, they do not overlap."* A tiled remainder is by construction a second
live row on the key. So the bitemporal arm **must** show >1 live row per key, and
the row-5 expectation of 0 restates precisely the naive `open <= 1` assertion
that §4 spends a paragraph explaining is the wrong test.

The decisive cell is row 4, the self-join, and it separates the arms exactly as
designed: **0 overlapping open pairs on the bitemporal arm against 10294 on the
control**, from the same 1060 multi-row keys. Same number of competing rows,
opposite answer on whether they conflict — which is the whole content of "they
tile rather than overlap", computed rather than trusted.

Nothing was weakened to obtain this. Rows 1–4 are reported as preregistered and
all pass; row 5 is reported as **failing as written on the bitemporal arm**, with
the reason it should never have been written that way.

### 8.3 Both arms saw the same corpus — checked, because the row counts differ

The arms do not carry the same number of rows, and that is worth pinning down
rather than assuming, because a 3599-row gap is 25% of the store:

| | bitemporal | A-recency |
|---|---|---|
| `preference` units | 10694 | **7095** |
| `episodic` units | 3394 | 3394 |
| total rows | 14088 | 10489 |
| of which `superseded` | 3599 | 0 |

`7095 + 3599 = 10694`. **Both arms retained the same 7095 preference assertions
and the same 3394 episodes**; the bitemporal arm's extra rows are the 3599
closed generations the machinery materialises. The control did not silently drop
a third of the corpus — corroborated independently by its 1060 subject keys
carrying competing live rows, which is ~every probe key: restatements did append.

### 8.4 Primary endpoint — latest-state-wins

Brackets are **cluster-bootstrap 95% CIs over the 257 instances**, 10,000
resamples, seed `20260801`. Arm A = full bitemporal, Arm B = A-recency.

| | **A — bitemporal** | **B — A-recency** | **Δ (A − B)** |
|---|---|---|---|
| **Latest-state-wins** | **0.6237** [0.5938, 0.6546] | **0.5936** [0.5629, 0.6256] | **+0.0301** [+0.0170, +0.0443] |
| **Misapplication** | **0.3396** [0.3118, 0.3672] | **0.3641** [0.3350, 0.3927] | **−0.0245** [−0.0376, −0.0125] |
| Neither returned | 0.0292 [0.0185, 0.0396] | 0.0348 [0.0220, 0.0473] | −0.0056 [−0.0124, +0.0010] |

| test (primary endpoint) | value |
|---|---|
| discordant pairs | **b = 47** (A only) / **c = 15** (B only), **n_d = 62** |
| exact two-sided McNemar | **p = 5.78 × 10⁻⁵** |
| cluster permutation (instance-level label flip, 10,000 perms) | **p = 1.00 × 10⁻⁴** |
| realized psi (this run's own cells) | **0.0583** |
| MDE at 80% power, n = 1063, realized psi | **0.0213** |

**n_d = 62, comfortably above the n_d ≥ 6 structural floor.** This is a
measurement, not an arithmetic null.

The two p-values agree to within a factor of two, so nothing is hiding in the
clustering; the bootstrap CI remains the verdict by prereg, and it excludes zero.

Secondary, descriptive, no test: `hit@1` 0.3857 vs 0.3810, `hit@10` 0.9247 vs
0.9087.

### 8.5 Verdict

**The bitemporal supersession machinery beats a plain `ORDER BY observed_at DESC`
recency tiebreak, given an identical correct subject key, by +3.01 points of
latest-state-wins (95% CI [+1.70, +4.43]) and −2.45 points of misapplication
(95% CI [−3.76, −1.25]), on 1063 probes over 257 instances.**

Both endpoints move the same way, both CIs exclude zero, and the effect is
roughly 1.4× the instrument's 80%-power MDE — so this is a real effect, and a
small one. The ~20-line control does **not** tie the edifice and does not beat
it. The question §1 posed is answered in the edifice's favour.

Three things that verdict does *not* say, all of which bound it:

1. **It is a comparison of resolution rules, not of MemPhant against anything.**
   Both arms are oracle-keyed off the same `topic` field the gold labels are
   built from. Neither number is comparable to the lexical baseline, and neither
   is evidence of retrieval quality.
2. **The margin is three points, against an edifice that costs a breaking
   migration to remove** (§5) and is the single largest source of complexity in
   the substrate (§1). "Earns its keep" is a measured *sign*, not a measured
   *cost-benefit*. Anyone deciding whether the complexity is worth paying for
   should weigh +3.0pp against §5's exit price, not treat significance as
   sufficiency.
3. **The transfer bound from §7 stands, and it cuts against the control.** The
   bitemporal arm prunes retired rows in SQL and never loads them; the control
   loads everything and prunes after fusion. That asymmetry is harmless *only*
   because no recall family truncates at this corpus's scale (~42 preference
   units per scope against a tightest cap of 100). At a production scope carrying
   200+ units per subject the most-recent-100 family truncates, the control
   spends pool slots on rows it is about to discard, and it would be handicapped
   by *where the prune sits* rather than by the recency rule. **This result does
   not carry to that regime.** It also means the measured +3.0pp is, if anything,
   the control's best case: the confound runs in the control's favour here and
   would run against it at scale. Anyone citing this number at production scope
   must re-measure or move the collapse into the store query.

### 8.6 A finding about the base, not about either arm

Arm A is the same oracle-keyed configuration as the 2026-07-31 Arm P, and it
reproduces that run's mechanism counts **exactly** — 7198 supersedes + 3599
contradicts edges, 3599 superseded units. Its score does not match:

| | Arm P (2026-07-31, `accuracy-first`) | Arm A (this run, `5d7b9d5a`) |
|---|---|---|
| latest-state-wins | 0.5795 | **0.6237** |
| misapplication | 0.3405 | **0.3396** |
| neither returned | 0.0734 | **0.0292** |
| `hit@10` | 0.840 | **0.925** |
| stale-outranks-current | 362 | 361 |

Misapplication and stale-outranks-current are unchanged. **The entire +4.4pp is
retrieval coverage**: the base got materially better at surfacing the rule at all
and no better at telling live from retired.

This is why re-running the ceiling arm was load-bearing rather than ceremonial.
Pairing today's control against the prereg's cited 0.5795 — measured on a
different tree — would have reported Δ ≈ −1.4pp *in the control's favour* and
inverted the verdict, out of pure base drift. **Both halves of a paired test must
be re-measured on the tree under test.** That is what the lineage stamp is for,
and this run is the first on this lane to carry one on both arms.

**And 0.6237 is not the new constant.** The correction above replaces a stale
ceiling with a fresher one; it does not make the fresher one stable. This number
moved 4.4pp in a single day on coverage alone and has no reason to have stopped,
so a reader who banks it is committing the same error one tree-generation later —
which is exactly the trap §8.6 was written about. Cite it as *"0.6237, ceiling
measured 2026-08-01 on `5d7b9d5a`"*, and re-cut it on whatever tree a claim is
being made against rather than carrying this one forward.

The durable quantities on this lane are the **deltas** — +0.0301 LSW,
−0.0245 misapplication, each with a CI and a discordant-pair count, both arms on
one tree and one binary. Those are properties of the *comparison*, which is why
they survive base drift that moves either arm's absolute rate. Any *share* or
*fraction-of-headroom* computed from an absolute rate is context with a date on
it, not a result. A corrected number is not automatically a stable one.

### 8.7 Reproduce

```bash
cd /Users/sidsharma/Memphant-w1-arecency          # branch w1-arecency @ 5d7b9d5a
docker start memphant-postgres-1
cargo build --release --bin memphant-server --bin memphant-worker --bin memphant-cli
OUT=docs/build-log/artifacts/2026-08-01-a-recency
SRC=~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet

# Arm A -- full bitemporal. ~54 min. Run DETACHED; see the note below.
<venv-with-pyarrow>/bin/python scripts/external_instrument_adapter.py \
  --instrument memorycode --arm preference --diagnostics --source $SRC \
  --out $OUT/arm-bitemporal.json --port 39541

# Arm B -- A-RECENCY CONTROL. ~43 min. Drops the subject exclusion constraint
# on its own scratch DB; see 2 and 5.
<venv-with-pyarrow>/bin/python scripts/external_instrument_adapter.py \
  --instrument memorycode --arm preference --a-recency --diagnostics --source $SRC \
  --out $OUT/arm-arecency.json --port 39542

python3 scripts/preference_lane_analysis.py \
  --arm-a $OUT/arm-bitemporal.json --arm-b $OUT/arm-arecency.json \
  --out $OUT/analysis-bitemporal-vs-arecency.json
python3 scripts/check_evidence_contract.py \
  --file $OUT/analysis-bitemporal-vs-arecency.json   # exits 0
```

**Operational note, because it cost 55 minutes.** A first attempt at arm A was
killed at ~90% of ingest by background-task eviction in the agent harness: a
stack of accumulated waiter tasks caused the oldest background task — the run
itself — to be reaped. Long benches must be launched fully detached
(`nohup caffeinate -is … &`) so nothing in the orchestration layer can reap them,
and their output must go to a file rather than through a buffering pipe, or the
diagnostics die with the process. Sibling lanes on this box lost time to the same
shape.


### 8.8 Two cross-checks that arrived after the verdict, both of which tighten it

Neither changes a measured cell. Both are recorded because each closes a hole a
reader would otherwise be right to poke at.

**(a) The control is gold-aligned, so it is a strong baseline, not a strawman.**
The sibling B1 lane (`w1-b1arm`, `a7f3e550`) measured a rate-matched *recency
ablation* and found it near-null against its no-op control (R2 − S0 = −0.0086),
then correctly refused to read that as evidence for semantics: an ablation that
*retires* the most recent live prior spends every edge on the rows most likely to
be golds, so it is a floor rather than a neutral baseline.

The same question has to be asked of A-recency, and it comes out the opposite
way. This control does not retire anything. It *selects* `max(observed_at)` per
`fact_key` — and on MemoryCode the gold unit is by construction the latest
session declaring that topic (`load_memorycode`: `occurrences[-1]`), with
`observed_at` assigned in session order. **The control's read rule therefore
computes the gold rule directly.** It is the most favourable trivial baseline this
instrument admits, not a weak one. The bitemporal arm beating it by +3.0pp is a
win over a baseline that was handed the answer's shape.

**(b) The §7 pruning-placement confound is now measured, not just argued, and it
did not bind.** §7 reasoned that the caps do not truncate at this scale. The run
gives a sharper check than the cap arithmetic:

| | rows the recall query can see |
|---|---|
| bitemporal (SQL filters `transaction_to` / `valid_to`) | 10489 active |
| A-recency (nothing to filter; every row open) | 10489 active |

**Exact parity.** The bitemporal arm's 3599 closed generations are excluded in
SQL; the control never mints them. So both arms present the *same number* of live
candidate rows to fusion, and the control is not spending pool slots on rows it
is about to discard — the mechanism by which §7 feared the confound could bite.
The confound is real in principle and inert here, for a reason stronger than the
cap margin.

Note what (b) does **not** rescue: the scale-conditional bound in §7 and §8.5
stands unchanged. That parity is a property of *this* corpus, where a subject key
carries ~3.4 assertions. Where a key carries many more, the control's pool fills
with older generations of the same key while the bitemporal arm's does not, and
the parity above breaks in the control's disfavour.

The residual coverage gap is small and runs the same way: `hit@10` 0.9247 vs
0.9087. With pool size held equal, that 1.6pp is the bitemporal arm ranking
better inside an identically-sized pool, not the control being starved of one.

**The sibling result, cited but kept out of these tables** (different arm pair,
different branch, different tree — `0ecf8cb2`): B1's structured extractor beats
its own no-op control by +0.0506 [+0.0269, +0.0753], but its *random* ablation
also beats that control by +0.0259 [+0.0088, +0.0432], leaving the semantic
increment at +0.0247 [−0.0033, +0.0532] — CI spanning zero, negative at their
preregistered bar. Read together with this lane: the bitemporal *resolution* rule
is worth ~3pp over the best trivial alternative (measured here), while the
*semantic target selection* that feeds it is not yet demonstrably worth more than
retiring an arbitrary prior (measured there). Those are separate claims about
separate stages, and they are not in tension.

Two near-inversions in one evening — this lane's from a stale banked ceiling
(§8.6), theirs from a mis-specified ablation — were each caught only by re-running
a control rather than citing one. **A banked ceiling is not a constant, and a
baseline that moves in the gold's direction is not neutral.**
