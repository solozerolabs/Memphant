# The MemoryCode as-of re-cut — S6

**Task S6 · branch `s6-asof` · 2026-08-01 · $0, no paid model call on any path.**
**Base:** `main` @ `6d95aebe` (S2's survey). **Worktree:** `/Users/sidsharma/Memphant-s6-asof`.

> **§1–§6 are the PREREGISTRATION.** They were committed before a single
> substrate-arm cell existed. §7 onward is the result.
>
> **What already existed when §1–§6 were written, stated exactly rather than
> implied:** the corpus census (§2) and the **complete trivial-baseline table
> (§4)**. Both are $0 offline computations over the pinned parquet with no
> database, and both are *inputs* to the design rather than the endpoint. **No
> arm of the substrate had been run, and the preregistered decision quantity —
> the arm-vs-arm delta — was entirely unmeasured.** Saying "preregistered"
> while a headline cell already exists is how this program has been burned;
> this note is the antidote, not a disclaimer.

---

## 1. The question, and why the banked wins are suspect

Two results carry this program's positive case, and both were measured on a
corpus that answers the question for us:

| banked result | ΔLSW | where |
|---|---:|---|
| bitemporal supersession − A-recency (Arm P, oracle-keyed) | **+0.0301** [+0.0170, +0.0443] | `2026-08-01-a-recency-control.md` §8.4 |
| Arm K (derived keys) − Arm A′ | **+0.058325** [+0.0382, +0.0791] | `2026-08-01-a4-prime-key-production.md` §4b |

MemoryCode's gold is **recency-identified by construction**
(`external_instrument_adapter.py`: `occurrences[-1]` is the gold, `observed_at`
assigned in session order). So `max(observed_at)` *is* the scoring rule, the
A-recency control computes it directly, and supersession arrives at it too.
**+3.01pp is the residual between two implementations of the same correct
rule.** Worse, wrong retirement is nearly free — 16 of 309 retirement edges
touch a gold (5.2%) — so the corpus **compresses** the effect. More probes
cannot fix either problem.

**S6 asks one question: do those two wins survive a cut of the same corpus that
does not flatter recency?**

## 2. The census — recomputed here, not inherited

From the pinned parquet (`memorycode.lock.json`, sha256 `1edb1238…`), using the
adapter's own key rule:

| quantity | S6 measured | S2 §1.1 |
|---|---:|---:|
| probe-bearing keys (declared ≥2×) | **1,063** | 1,063 |
| declarations inside those keys | **4,679** | 4,679 |
| current-vs-stale pairs Σ(m−1) | **3,616** | 3,616 |
| **as-of probes** | **3,599** | 3,608 |
| degenerate, dropped | **17** | 8 |
| contributing instances | **257** | 257 |
| **keys where a value is re-asserted after replacement** | **0** | 0 |

**The 3,599-vs-3,608 gap is a definitional difference, not an error in either
document, and it matters for what follows.** S2 dropped a probe when the *final*
declaration shares a session index. S6 drops a probe whenever **any** consecutive
pair shares one, because two declarations of a key inside one session leave no
interval during which the earlier was uniquely in force — there is no as-of
question to ask. S6 uses its own 3,599 and every power figure below is computed
from it.

**The zero re-assertion count is load-bearing and it is confirmed.** A key's
declarations form a strictly monotone chain. Regime (a) is structurally absent
and no re-cut can add it.

## 3. The construction, and the correction S2's spec needs

For a key declared at sessions j₁ < … < j_m, S6 emits m−1 probes, one per
non-final declaration r:

```
valid_at       = midpoint(observed_at(j_r), observed_at(j_{r+1}))
gold           = j_r
distractors    = every OTHER declaring session, including j_{r+1} … j_m
```

Ingest is unchanged: the whole corpus is still present, so `max(observed_at)`
returns j_m — **a distractor — on every probe by construction.**

### 3.1 S2 §4.1 specifies `valid_at = observed_at(j_r) + ε`. That is degenerate, and it was measured before it was adopted

| valid_at policy | probes where the latest session of ANY kind at or before t is the gold |
|---|---:|
| **ε (S2 §4.1 as written)** | **3,599 / 3,599 = 100%** |
| **midpoint (S6 adopts)** | **667 / 3,599 = 18.5%** |

Under the ε policy the trivial rule "take the newest session at or before t"
**is the gold rule exactly** — the re-cut would reproduce the very
identification it exists to break, one axis over. Under the midpoint policy
2,932 probes have at least one non-declaring session between j_r and t, so the
naive answer is a non-declaring session. The midpoint is also the semantically
honest reading: *as of a time while rule r was in force*, not *one epsilon after
it was stated*.

### 3.2 What the midpoint still does not fix — stated here, ahead of every number it bounds

**Among the DECLARING sessions of a key, `max(observed_at ≤ t)` is still exactly
j_r on every probe**, because the chain is monotone (§2: zero re-assertions).
Any rule that truncates at t and then takes the newest therefore ranks the gold
above every distractor it returns, and can never rank a distractor first.

**Consequence, and it is the central limit of this instrument: the gold-vs-stale
endpoint (`appropriate_application` / latest-state-wins, the endpoint both
banked wins are measured on) is SATURATED by the trivial as-of rule, up to pool
coverage.** That is a property of MemoryCode, not of this loader, and it cannot
be re-cut away. `hit@1` is the endpoint where the rules genuinely separate.
**Both are reported, and no LSW number below may be read without this
paragraph.**

## 4. The FULL trivial-baseline set — preregistered as a set, and run as a set

S2 §3.3's trap, measured on TempLAMA's own shipped files: `max(timestamp)` is
correct on 55.5% of rows and `most_frequent_answer` on **70.7%**. Beating the
recency control proves nothing until the other cheap rules have been run.

All six rank the same haystack the substrate arms recall over — the instance's
own sessions, the scope `bind_context` binds — so they sit at the same stage.
All are $0: no database, no server, no network, no model call.

| rule | what it is | **LSW** | **hit@1** | misapplication | neither returned |
|---|---|---:|---:|---:|---:|
| `recency` | `max(observed_at)` — the A-recency rule, untruncated | **0.0000** | 0.0000 | 0.5760 | 0.4240 |
| `constant` | session order, first session first — the floor | 0.1450 | 0.0378 | 0.3832 | 0.4718 |
| `bm25` | the repo's own BM25, no time signal | 0.1989 | 0.1631 | 0.8005 | 0.0006 |
| `mode_oracle` | `most_frequent_answer` — **ORACLE-KEYED, not comparable** | 0.2945 | 0.2945 | 0.7055 | 0.0000 |
| `bm25_asof` | relevance, then drop anything asserted after t | 0.5093 | **0.4121** | 0.4876 | 0.0031 |
| **`asof_truncation`** | **`max(observed_at ≤ t)` — the honest baseline** | **0.9064** | 0.1853 | **0.0000** | 0.0936 |

*(3,599 probes / 257 instances, k=10, same scorer as every arm. Dated
2026-08-01; absolute rates have a shelf life, the ordering is the durable part.)*

**Three things this table establishes before any arm runs.**

1. **The re-cut does what it was built to do.** `max(observed_at)` scores
   **exactly 0.0000** LSW — provably wrong on every probe, from 0.5936 on the
   original cut. The corpus no longer flatters recency.
2. **It replaced one identity with another.** `max(observed_at ≤ t)` scores
   **0.9064 LSW with misapplication of exactly 0.0000** — it is analytically
   incapable of ranking a distractor first (§3.2), and its only failure is
   top-k truncation. **A ~20-line read rule with no substrate at all is the
   number to beat.**
3. **The endpoints disagree, so quoting one is quoting half.** On hit@1
   `bm25_asof` (0.4121) beats `asof_truncation` (0.1853); on LSW the ordering
   reverses by 40 points. `mode_oracle` does not win here, so S2's
   `argmax(count)` trap is checked and cleared **on this corpus** — that is a
   negative result about this corpus, not a general acquittal of the rule.

## 5. Arms, and the PREREGISTERED DIRECTION

| # | arm | what it is |
|---|---|---|
| A′ | `--arm memphant` | default episode ingest. `MEMPHANT_FACT_EXTRACTION` now defaults **ON**, so on-tree A′ is configurationally the old **Arm F** and is labelled that way |
| P | `--arm preference --bounded` | oracle-keyed bitemporal supersession — the arm carrying **+3.01pp** |
| P⁻ | `--arm preference --bounded --a-recency` | the A-recency control, same key |
| K | `--arm derived --bounded` | `pre3_content_words` gold-independent keys — the arm carrying **+0.0583** |
| T | `--arm trivial` ×6 | §4, already run |

**PREDICTED DIRECTION, WRITTEN DOWN BEFORE LOOKING.**

1. **Δ(P − P⁻) will GROW, massively — and that will NOT be evidence for the
   edifice.** Bounded ingest makes supersession tile the valid axis: each
   declaration j_r ends as a rectangle `[obs(j_r), obs(j_{r+1}))` carrying j_r's
   body, so exactly one rectangle contains t and it is the gold. P should land
   near `asof_truncation`. P⁻ never sets a valid interval, so its read rule
   returns j_m and should land near the measured **0.0000**. A Δ of +0.8 is
   predicted and is **an artifact of the control being provably wrong by
   construction**, not a measured benefit. **The +3.01pp does not "grow to
   +80pp"; the two numbers are not on a common scale and must never be
   subtracted or compared.**
2. **The decision quantity is therefore `Δ = LSW(P) − LSW(asof_truncation)`, not
   `LSW(P) − LSW(P⁻)`.** Predicted **≈ 0 or negative**: the substrate should not
   beat a rule that is analytically incapable of a misapplication.
3. **Δ(K − A′) is predicted to persist in SIGN but shrink**, because K's keys
   are lossy: where the derived key fails to match, no interval is ever closed,
   the session stays an unbounded unit and is returned at every t.
4. **Absolute levels will move in both directions and none of them is a
   regression.** A level is not a result here. Only Δ is.

## 6. Gates, analysis, and the floor

**MECHANISM LIVENESS BEFORE ACCURACY.** Read from each arm's own scratch DB on
the bench superuser credential, before any score:

| check | P (bitemporal) | P⁻ (A-recency) | K |
|---|---|---|---|
| `supersedes` edges | > 0 | **0** | > 0 |
| valid-closed rows | > 0 | **0** | > 0 |
| open rows sharing a subject key with **overlapping** valid ranges | **0** | > 0 | **0** |
| corpus compilation, asserted from the DB | clean | clean | clean |
| **`remainders_recalled`** | **> 0** | 0 | **> 0** |

**The last row inverts every prior lane's expectation and that inversion is the
whole point.** On the original cut a recalled remainder was a scoring hazard and
`0` was the pass. Here the historical rectangle **is the gold answer**, so `> 0`
is the pass and **`0` means the valid-time machinery never fired — "INERT" is
then the whole report** and no score is read, exactly as Arm F was voided.

**Zero `supersedes` edges ⇒ "inert" is the whole report.**

**Remainder attribution is a correctness precondition, not a detail.** The
remainder is returned in the *superseding* retain's `unit_ids`. Crediting it to
that session — which every prior run did, safely, because remainders were never
recallable — would attribute every correct as-of answer to a **distractor** and
score the bitemporal arm near zero for a bookkeeping reason. S6 credits the
leading ids of a supersede response to the session that last asserted the key.

**Analysis.** Primary: paired exact two-sided McNemar on LSW, with b, c and the
per-item vectors preserved. Cluster bootstrap over the **257** instances (10,000
resamples, seed `20260801`) reported alongside and **it is the verdict by
prereg**, because probes nest at mean **14.0** per instance (max 71) and the
McNemar is anti-conservative here. Realized ψ and MDE computed from **this run's
own cells** via `scripts/instrument_power.py` — no figure inherited, including
S2's.

**The n_d ≥ 6 structural floor.** Exact two-sided McNemar has no rejection region
below six discordant pairs. Below it the verdict is written **"NOT A
MEASUREMENT"** plus the required n — never "a tie", never "no effect".

**Lineage or it did not happen.** Git HEAD, branch, dirty flag and the sha256 of
the served server and worker binaries are stamped into every artifact. All arms
run on **one tree, the same binaries, the same stage, the same haystack.**

**$0.** No paid arm on any path. `paid_model_calls: 0` asserted in every report.

---

## 6b. PREREG AMENDMENT — written after the 20-group liveness gate, before any full-n arm

**Exactly what existed when this section was written, and nothing more:** the
§4 trivial table, and the **20-group / 25-probe liveness smoke** of arm P
(§7.1). No full-n substrate cell existed; the first full-n arm was launched
after this section was committed. This amendment is disclosed as an amendment
rather than folded back into §5, because silently improving a prediction after
seeing a pilot is how a prereg becomes decoration.

**The smoke is a pilot, not a result — n=25, one arm, no control.** It is
recorded here because it changes the *expected magnitude*, and because
withholding it until it agreed with a prediction would be the exact failure §5
exists to prevent.

**Amended prediction: the P-vs-trivial gap is expected to be SMALL OR NULL, and
"the instrument does not discriminate" is the anticipated and fully valuable
outcome.** Arm P measured **LSW 0.92** on the smoke against `asof_truncation`'s
**0.9064** at full n — a ~1.4pp gap that is far inside noise at n=25 and is
**not** evidence of a gap. That is exactly what §3.2 predicts analytically: with
zero re-assertions the declaration chain is monotone, so a truncating rule
cannot rank a distractor first, and there is almost nothing left for the
substrate to win.

**This amendment therefore LOWERS the expected effect; it does not license a
search for a larger one.** No stratum will be promoted to primary after the
fact, no threshold will be tuned, and the endpoint stays LSW with hit@1 as the
preregistered secondary. If the full-n gap is small, null, or negative, that is
the finding.

**Corroboration from sibling lanes, which is why the amendment is stated as a
general claim rather than a local one.** S7 pinned MDN browser-compat-data —
CC0, **705 genuine re-assertion arcs**, the exact regime MemoryCode structurally
lacks — and a ~20-line `scoped_interval` rule scores **1.0000 on every band**.
So **re-assertion and non-recency currency are not the missing ingredients**;
both are present there and neither is sufficient.

**The general claim this lane is the cleanest demonstration of, preregistered
here as the conclusion to test rather than discovered afterwards:**

> **A corpus whose gold is computable from the fact statements themselves will
> always be saturated by a short rule.** MemoryCode's gold is a function of the
> declaration sequence — latest on the original cut, latest-before-t on the
> as-of cut — so some ~20-line rule computes it either way. Re-cutting moves
> *which* short rule wins; it cannot make the gold uncomputable from the
> statements, and no amount of probes or power changes that.

**What would falsify it (see §12 — it did not):** an instrument whose gold depends on evidence outside
the statement set — execution, external authority, or scope the statements do
not carry — such that no rule over the statements alone recovers it. That is a
property of the *source of truth*, not of the temporal construction, and it is
the specification any future instrument spend must satisfy **before** it is
bought.

---

## 7. Results

**All four substrate arms ran on ONE tree (`12b4ebe1`) from the SAME two
binaries** — server `dcf570b5068d…`, worker `5d15575663e2…`, sha256 stamped into
every artifact — over the same 3,599 probes / 257 instances, at the same stage,
on the same haystack. `paid_model_calls: 0` in all ten reports.

### 7.1 Mechanism liveness — from each arm's own scratch DB, before any score

| check | prereg expects | **A′** | **P** | **P⁻** | **K** |
|---|---|---:|---:|---:|---:|
| `supersedes` edges | >0 (P,K) / 0 (P⁻) | **0** | **7,198** | **0** | **4,162** |
| valid-closed rows | >0 (P,K) / 0 (P⁻) | 0 | **3,599** | **0** | **2,081** |
| open rows sharing a key with **overlapping** valid ranges | 0 (P,K) / >0 (P⁻) | 0 | **0** | **10,294** | **0** |
| **`remainders_recalled`** | **>0** (P,K) / 0 (P⁻) | 0 | **9,617** | **0** | **5,657** |
| compilation asserted from the DB | clean | clean | clean | clean | clean |

**The gate passes in both directions, and the inverted row is the one that
matters.** `remainders_recalled` is **9,617** on P and **5,657** on K — the
first non-zero in this program's history, against `0` in every prior lane. The
historical rectangle `[obs(j_r), obs(j_{r+1}))`, carrying the prior body, is
being minted by bounded supersession and **returned as the as-of answer**. The
valid-time machinery is live, not inert, and this instrument is the first thing
built here that exercises it.

**A′ is INERT and that bounds what K − A′ means.** `MEMPHANT_FACT_EXTRACTION`
defaults ON, so on-tree A′ is configurationally the old **Arm F** — and it
minted **zero** supersede edges, zero keyed units, zero valid-closed rows. So
**K − A′ is "keyed writes versus no keyed writes at all", not "a good key versus
a worse key."** It is a much coarser contrast than its banked +0.0583 ancestor,
and must not be quoted as the same comparison.

### 7.2 Levels — every arm, every baseline, one table

Per §5(4): **a level is not a result here, and both banked MemoryCode levels are
shown beside them so no reader can quote one in isolation.**

| | **LSW** | **hit@1** | hit@k | misapp | neither | (banked, original cut) |
|---|---:|---:|---:|---:|---:|---|
| **`t-recency`** `max(observed_at)` | **0.000000** | 0.000000 | 0.163101 | 0.575993 | 0.424007 | A-recency was 0.5936 |
| `t-constant` session order | 0.145040 | 0.037788 | 0.298972 | 0.383162 | 0.471798 | — |
| `t-bm25` no time signal | 0.198944 | 0.163101 | 0.896916 | 0.800500 | 0.000556 | — |
| **A′** default ingest (= old Arm F) | 0.198944 | 0.152820 | 0.766602 | 0.797444 | 0.003612 | 0.3142 |
| `t-mode_oracle` **oracle, not comparable** | 0.294526 | 0.294526 | 1.000000 | 0.705474 | 0.000000 | — |
| `t-bm25_asof` relevance then truncate | 0.509308 | 0.412059 | 0.973882 | 0.487635 | 0.003056 | — |
| **K** derived keys | 0.552098 | 0.366491 | 0.871909 | 0.414282 | 0.033620 | 0.3725 |
| **P⁻** A-recency control | 0.728258 | 0.468741 | 0.948319 | 0.240067 | 0.031675 | 0.5936 |
| **P** bitemporal | 0.739928 | **0.471798** | 0.947485 | 0.228675 | 0.031398 | 0.6237 |
| **`t-asof_truncation`** `max(observed_at ≤ t)` | **0.906363** | 0.185329 | 0.906363 | **0.000000** | 0.093637 | — |

*(2026-08-02. Absolute rates have a shelf life; the deltas below are the durable part.)*

**A′ and `t-bm25` agree to six decimal places and that is a coincidence, checked
rather than assumed:** 512 probes disagree, at **b = 256 / c = 256** — perfectly
balanced discordance, not an identical ranking.

### 7.3 The decision quantity — preregistered in §5(2) as `P − asof_truncation`

Cluster bootstrap over the 257 instances, 10,000 resamples, seed `20260801`.
Realized ψ and MDE computed from **this run's own cells**; nothing inherited.

| comparison | endpoint | **Δ** | cluster CI95 | b / c | n_d | ψ | **MDE80 flat** | **MDE80 @DEFF 8** |
|---|---|---:|---|---:|---:|---:|---:|---:|
| **P − `asof_truncation`** | **LSW** | **−0.166435** | **[−0.1902, −0.1435]** | 268 / 867 | 1,135 | 0.3154 | 0.0265 | 0.0759 |
| P − `asof_truncation` | hit@1 | **+0.286468** | [+0.2628, +0.3115] | 1314 / 283 | 1,597 | — | — | — |
| P − `asof_truncation` | misapp | +0.228675 | [+0.2106, +0.2468] | 823 / **0** | 823 | — | — | — |
| **P − P⁻** | **LSW** | **+0.011670** | **[+0.0053, +0.0182]** | 102 / 60 | 162 | 0.0450 | **0.0101** | **0.0287** |
| P − P⁻ | hit@1 | +0.003056 | **[−0.0053, +0.0116]** | 123 / 112 | 235 | — | — | — |
| K − A′ | LSW | +0.353154 | [+0.3335, +0.3745] | 1306 / 35 | 1,341 | 0.3726 | 0.0288 | 0.0824 |
| K − `asof_truncation` | LSW | −0.354265 | [−0.3796, −0.3288] | 189 / 1464 | 1,653 | 0.4593 | 0.0319 | 0.0914 |

**Every n_d is far above the n_d ≥ 6 floor. Every cell here is a measurement.**

## 8. Verdict

### 8.1 The substrate loses to a 20-line read rule, and not narrowly

**At n = 3,599 over 257 instances, on a cut where `max(observed_at)` is provably
wrong on every probe: the full bitemporal supersession machinery scores
0.7399 latest-state-wins against 0.9064 for `max(observed_at ≤ t)` — a ~20-line
read rule with no substrate, no supersession, no valid-time columns and no
database. Δ = −0.1664, cluster CI95 [−0.1902, −0.1435], 6.3× the computed MDE
even at design effect 8.**

Every substrate arm loses to it: P by 16.6pp, K by 35.4pp, A′ by 70.7pp. And the
trivial rule's **misapplication is exactly 0.000000, with c = 0 discordant pairs
against P** — there is not one probe in 3,599 where it ranks a retired rule above
the live one, because §3.2 says it *cannot*. **The trivial rule does not tie the
substrate. It beats it decisively, and it beats it for a structural reason.**

### 8.2 The bitemporal machinery buys ~1pp over its own control — significant and negligible, and both words are required

**P − P⁻ = +0.0117 LSW, cluster CI95 [+0.0053, +0.0182], exact McNemar
p = 1.2 × 10⁻³, n_d = 162.** The CI excludes zero, so by the preregistered rule
this is a **positive**.

**It is also substantively negligible, and the power arithmetic is what shows
it.** The effect is 1.17pp against a computed MDE of **1.01pp flat** — it clears
significance by a hair — and it sits **below the 2.87pp MDE at design effect 8**,
which is the regime this instrument actually lives in (probes nest at mean 14.0
per instance). On **hit@1 the same comparison is null**: +0.0031, CI
**[−0.0053, +0.0116]**, spanning zero.

**"Statistically significant" and "matters" diverge here and must not be
conflated.** The honest sentence is: *supersession beats its own recency control
by about one point of LSW and by nothing at all on hit@1, on 3,599 probes.*
Anyone reporting the p-value without the magnitude, or the magnitude without the
DEFF-8 MDE, is reporting half of it.

### 8.3 Do the banked wins survive? — the direct answer

**The +3.01pp: it does not survive as the claim it was made as.** On the as-of
cut the same comparison is **+1.17pp**, and it is dwarfed by a trivial rule that
beats the winning arm by 16.6pp. The banked figure was never wrong arithmetically
— it is the residual between two implementations of the same correct rule, exactly
as `a-recency` §8.5 said. **What S6 adds is that removing the flattery does not
rescue it: it shrinks it to ~1pp and simultaneously reveals that the whole
contest was being held below a baseline nobody had run.**

**The +0.0583: it does not survive as the same comparison.** K − A′ measures
**+0.3531** here, but that is *larger* for a disqualifying reason — A′ is **inert**
on this tree (§7.1: zero edges), so the contrast is keyed-versus-unkeyed, not
key-quality. Against the honest baseline, **K − `asof_truncation` = −0.3543**
[−0.3796, −0.3288]. **Arm K loses to the 20-line rule by 35 points.**

**Neither banked win survives contact with a corpus that does not flatter
recency and with a baseline set that was run in full.**

### 8.4 What the substrate does win, reported because it is real

**P beats every trivial rule on hit@1**: 0.4718 vs 0.4121 for `bm25_asof`
(+0.0597) and vs 0.1853 for `asof_truncation` (**+0.2865**, CI [+0.2628,
+0.3115]). The truncation rule ranks by time, so it puts the gold at rank
*(number of intervening sessions)* — it gets the *ordering* of gold-before-stale
right by construction while burying the gold below noise. P ranks by relevance
*within* what is valid at t, and puts the right session first far more often.

**This is a genuine and preregistered-secondary win, and it does not rescue
§8.1.** A system that answers correctly at rank 1 more often, while losing the
primary endpoint by 16.6pp to a rule you could write in an afternoon, has not
earned an edifice that costs a breaking migration to remove
(`a-recency` §5).

### 8.5 The prereg miss, reported as a miss

**§5 predicted P⁻ would land near the trivial `recency` rule's 0.0000. It
measured 0.7283 — the prediction was wrong, and the reason is mechanical.**
`crates/memphant-store-postgres/src/store.rs:2298` applies

```sql
and coalesce(valid_from, '-infinity'::timestamptz) <= $9::timestamptz
```

**unconditionally — the filter is not gated on `MEMPHANT_A_RECENCY_CONTROL`.**
So with bounded ingest the control inherits as-of truncation *for free* from the
store, and P⁻ is not "A-recency"; it is **A-recency + as-of truncation realized
inside the substrate**, which is precisely S2 §4.2's "arm 2" sharing an identical
retrieval stack with P.

That accident produced a **better** pairing than the one designed — the honest
baseline and the edifice differing only in whether generations close — and it is
what makes §8.2's +1.17pp the cleanly interpretable number it is. **The
prediction was still wrong and is recorded as wrong.** The untruncated control is
the `t-recency` row, which measures the predicted **0.000000**.

## 9. The ranking-not-retrieval finding — the fourth independent instance today

**Arm P retrieves the answer far more often than it answers with it: hit@k
0.9475 against LSW 0.7399.** The gold is in the returned pool for 94.8% of
probes and wins the probe for 74.0% — **a ~21-point gap that is a ranking loss,
not a retrieval loss.** Three sibling lanes hit the same wall today from three
different directions:

| lane | in-pool | ranked/answered | gap |
|---|---:|---:|---:|
| **S6 (here)**, arm P, as-of MemoryCode | hit@k **0.9475** | LSW **0.7399** | **21.1pp** |
| S4, repo-recoverable facts | gold in pool **93.9%** | at rank ≤10 **62.8%** | 31.1pp |
| S8, LLM ranker over our own pool | perfect ranker **0.911** | RankAcc **1.000** at $0 | — |

**Four independent instances, on different corpora and different endpoints, all
saying the retrieval stage is not the binding constraint.** For this lane it is
sharper still: P's pool is *better* than the trivial rule's (hit@k 0.9475 vs
0.9064) and its answer is *worse* (0.7399 vs 0.9064). **We are retrieving the
right thing and then ranking it away.** That is where the next engineering pound
belongs, and it is cheap — S8 measured a perfect ranker at $0 over the pool we
already have.

## 10. What this instrument cost and what it is worth

**$0.** No paid model call on any path, in any of the ten reports. Build was one
loader, `valid_at` threaded into recall, `valid_from` on the unit payload, a
remainder-attribution rule, and six trivial baselines.

**Keep it.** It is the only instrument in this repo that exercises valid-time at
all (`remainders_recalled` 9,617 vs 0 everywhere else), and it is the only place
the bitemporal machinery can be shown to fire end-to-end on the construct it
exists for. **But do not run a decision on it**: §3.2 is structural, and the
primary endpoint is saturated.

## 11. What must NOT be concluded

1. **Not "supersession regressed."** §5(1) preregistered the level drop. P went
   0.6237 → 0.7399; the levels are on different cuts and **are not comparable**.
   Only Δ is.
2. **Not "the edifice is worthless."** It is worth ~1pp of LSW over its own
   control on this corpus (§8.2), and it wins hit@1 (§8.4). What is established
   is that **it does not earn a breaking-migration exit price here**, and that
   the contest was being run below an unrun baseline.
3. **Not "MemPhant loses to BM25."** `asof_truncation` is not a retrieval system;
   it is a time filter over a bound instance haystack of ~32 sessions. It does
   not scale, has no notion of relevance (hit@1 0.1853), and would be useless at
   production scope. **What it proves is about the INSTRUMENT, not the product.**
4. **Not a scale claim.** `a-recency` §7's caveat binds unchanged: at ~32–42
   units per scope no recall family truncates. None of this transfers to a scope
   carrying 200+ units per subject.

## 12. The conclusion this lane exists to support — preregistered in §6b, confirmed harder than predicted

> **A corpus whose gold is computable from the fact statements themselves will
> always be saturated by a short rule.**

MemoryCode's gold is a function of the declaration sequence: latest on the
original cut, latest-before-t on the as-of cut. A ~20-line rule computes it
either way — 0.5936 then, **0.9064** now. **Re-cutting changed which short rule
wins; it did not make the gold uncomputable from the statements.** §6b predicted
a small-or-null gap between substrate and trivial rule; the measured gap is
**−16.6pp against the substrate**, so the conclusion is confirmed in a stronger
form than predicted — the trivial rule does not tie, it wins.

**S7 closes the argument from the opposite direction.** MDN browser-compat-data
is CC0, carries **705 genuine re-assertion arcs** — regime (a), which MemoryCode
structurally lacks (§2: zero) — and a ~20-line `scoped_interval` rule scores
**1.0000 on every band**. **So re-assertion and non-recency currency are NOT the
missing ingredients.** Both are present there; neither is sufficient. The
missing ingredient is not a temporal construction at all.

**The falsification condition, restated as the specification any future
instrument spend must satisfy BEFORE it is bought:** the gold must depend on
evidence **outside the statement set** — execution, external authority, or scope
the statements do not carry — such that no rule over the statements alone
recovers it. S4's result points the same way: an agent with `grep` beat MemPhant
96.67% to 58.89% (p = 1.2 × 10⁻¹⁹) on facts recoverable from the repo, because
the repo *is* the evidence outside the statements.

**No default, checkbox, cutover or SOTA claim moves on this result.
`paid_model_calls: 0`.**
