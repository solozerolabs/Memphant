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

**What would falsify it:** an instrument whose gold depends on evidence outside
the statement set — execution, external authority, or scope the statements do
not carry — such that no rule over the statements alone recovers it. That is a
property of the *source of truth*, not of the temporal construction, and it is
the specification any future instrument spend must satisfy **before** it is
bought.
