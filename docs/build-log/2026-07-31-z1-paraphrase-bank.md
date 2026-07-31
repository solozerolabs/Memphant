# Z1 — the Track R ladder re-measured on the decontaminated paraphrase bank

**Status: DIAGNOSTIC, NOT PROMOTION-GRADE.** Paid API spend: **$0**. No default,
gate or config moves here.

**Headline, stated first because it costs us a banked claim: dense retrieval was
killed on a contaminated instrument and the kill does not survive
decontamination.** On the original bank, dense stacked on `bm25-code` was −10/+3
at k=5 (p=0.200) and 16/15 at k=10 (p=1.000) against the scoped BM25 control, and
the banked conclusion was "the best configuration uses no embeddings at all". On
the paraphrase bank the same stack is **b=29 / c=5 at fused@10 (p=3.86e-05,
power 0.99)** against `bm25-code` alone, and **b=71 / c=4 (p=6.8e-17)** against
the control. The null was an artifact of a bank that leaked 3.93× lexically. The
claim "embeddings add nothing to the coding lane" must be **withdrawn**.

The second finding, also fused-stage and also sound: the retrieval win over the
scoped BM25 control did **not** shrink on clean data. It grew — b=15/c=3 →
b=48/c=5 at fused@10.

> ## LINEAGE WARNING — read before using any packed number in this document
>
> The arms scored here ran at `af-w0-instrument` `3e2cc2ba` (worktree git_head
> `742e2e6a`). **Both render fixes are absent from that branch**, verified by
> ancestry:
>
> | commit | subject | in `af-w0-instrument` | on `accuracy-first` |
> |---|---|---|---|
> | `f67f2b2a` | let a partially chunk-rendered item emit its whole body | **NO — absent** | YES |
> | `3fc4eede` | scale the Exact channel by its own subject-key coverage | **NO — absent** | YES |
>
> `f67f2b2a` is the render-loss fix that took provenance from 163 to 1,193 items
> and moved the original-bank packed ladder to 168/180.
>
> **Fused-stage numbers are unaffected and stand.** Render loss is a
> packing-stage defect; it does not touch retrieval or fusion. The dense finding,
> the control comparison and every `fused@k` cell in §4 are sound.
>
> **Every packed-stage number in this document is `LINEAGE-STALE
> (pre-f67f2b2a)`** and is *not* comparable to any banked post-fix figure. A
> comparison between them moves two variables at once — bank contamination *and*
> render lineage — and can support no conclusion about either.

---

## 0. Scope correction — this was not the first execution

The task framing ("the first-ever execution of the Track R paraphrase golden
bank") and the register's statement that the paraphrase arms were "verified as
runnable but not executed" are both **stale**. Four paraphrase arms plus the
scoped BM25 control were executed on 2026-07-30 from the `af-w0-instrument`
worktree and committed there as `3e2cc2ba` with
`docs/build-log/artifacts/track-r-paraphrase/w0-2-five-arm.json` and
`docs/build-log/2026-07-31-w0-2-paraphrase-arms.md`.

Rather than burn ~6 arm-hours re-running deterministic arms to reproduce
committed numbers, this pass does four things the prior run did not:

1. **Independently re-verifies every precondition** (§1) rather than inheriting them.
2. **Recomputes every paired cell from the raw provenance** with an independent
   script, and confirms it reproduces the committed cells exactly (§2).
3. **Attaches realized-ψ exact-McNemar power and MDE to every contrast** — the
   W0.2 log reports p-values with no power analysis at all. This is register
   action Z6 (§4).
4. **Establishes that the prior run's packed figures are lineage-stale** (§5) —
   they predate both render fixes, so the packed half of that comparison, and the
   packed half of this one, support no conclusion.

**One arm from the assignment is genuinely missing and is NOT reported here:**
the textbook-BM25 attribution arm (`--lexical-scorer bm25-control`). See §6.

## 1. Preconditions — verified, not assumed

All checks were re-run in this worktree against the private bodies at
`~/.memphant-private/track-r-paraphrase/` (read-only).

| precondition | required | observed | verdict |
|---|---|---|---|
| row count | 180 | 180 | PASS |
| golden sha256 vs lock | match | `4aed8e99…4326` both sides | PASS |
| provenance spans per golden | exactly 1 (runner hard-requires) | `{1: 180}` — no golden with 0 or ≥2 | PASS |
| blank spans | 0 | 0 | PASS |
| abstentions | 0 | 0 | PASS |
| corpus sha256 | `c008142e…` | `c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669` | PASS |
| shape balance | — | 60 / 60 / 60 across the three shapes | PASS |

The corpus sha is byte-identical to the one the original-bank arms ran on, so the
two banks are compared over the **same haystack**. The single-span check matters
because `scripts/track_r_retrieval_arm_compare.py:85-87` raises rather than
degrades if it fails; the fused-rank → provenance-hit equivalence this whole
comparison rests on is only valid for single-span goldens.

**Bar checks: 20 of 21 pass**, not 19 of 20 as the register §4.2 table states.
The register's count is off by one in both numerator and denominator; the
substance is unchanged. The single failure is `leak_concentration_le_1_50`, the
bar the register itself argues is mis-set below the achievable floor.

## 2. Mechanism liveness and queue drain

Read from each arm's own provenance report, not from logs.

| arm | `lexical_scorer` | `embed_model` | compile s | recall s | done_jobs | pending | dead | units |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `overlap_off` | overlap | off | 2673.1 | 746.8 | 64056 | 0 | 0 | 64014 |
| `bm25code_off` | bm25-code | off | 2377.4 | 1011.1 | 64056 | 0 | 0 | 64014 |
| `overlap_dense` | overlap | small | 4826.3 | 659.6 | 64056 | 0 | 0 | 64014 |
| `bm25code_dense` | bm25-code | small | 4727.1 | 718.9 | 64056 | 0 | 0 | 64014 |

**Drain: `done_jobs=64056, pending_jobs=0, dead_jobs=0`, 64,014 units after 42
exact-duplicate dedups, on every arm.** These are byte-identical to the figures
the original-bank arms recorded, so no deviation to flag and no partially-drained
corpus understating recall.

**Scorer liveness.** The scorer is not merely declared: the four arms produce four
materially different recall vectors (fused r@10 0.2222 / 0.4944 / 0.4611 / 0.6278)
and differ pairwise at b/c counts far outside chance (§4). A mis-wired flag would
show as identical arms; none are.

**Embedder liveness.** The two `embed_model=small` arms show compile time roughly
**doubled** (4727s, 4826s) against the two `off` arms (2377s, 2673s) over an
identical 64,056-job corpus. That extra ~2,200 seconds is embedding work actually
performed. The embedder engaged.

**Contention.** `loadavg 78.67 → 115.78` on `cpu_count = 12` during this analysis
session. **No latency or SLO figure is reported from this window.** The timings
above are used only as liveness evidence (a ratio between arms measured on the
prior, quieter session), never as performance numbers.

## 3. Leakage — all five fields, for the bank actually run

1. **Unit definition:** `coverage(question, event) = |T(question) ∩ T(event)| / |T(question)|`
   where `T(s) = set(re.findall(r'[a-z0-9_]{3,}', s.lower()))`. Computed by
   `scripts/track_r_leakage.py`.
2. **Absolute target coverage:** mean **0.1346** (median 0.1286, p10 0.0323,
   p90 0.2558, max 0.5). *Not portable across unit definitions.*
3. **Floor: 0.0667, EXHAUSTIVE** — the mean coverage over *every* non-target event
   of the same attempt, no seed and no draw. (Sampled floor, seed 7: 0.0656.)
4. **Concentration: 2.018× exhaustive** (2.0518× sampled).
5. **Provenance class:** paraphrase — question generated with the target's
   identifiers **withheld**, mean 19.45 withheld terms per query, mean lexical
   overlap 0.0162 (max 0.1064), 180/180 goldens with adjudicated distractors
   (900 total), 134 distinct repositories, 155 distinct attempts.

Contrast, original bank: target 0.3960 / floor 0.1008 / **3.9286×**, provenance
class *agent-generated from the target*, 75/180 adjudicated distractors. Excess
over floor falls **0.77** from original to paraphrase.

The paraphrase bank fails its own preregistered `≤1.50×` bar at 2.018×. That bar
sits below the measured achievable floor of 1.79× and below the independent human
band 1.76–2.03×, which 2.018× is inside. Every number below is therefore reported
as diagnostic, with the failure declared, exactly as the W0.2 run did.

## 4. The arms — full 2×2 cells, realized ψ, power and MDE

n = 180 paired throughout. `b` = questions the **left** arm got and the right did
not; `c` = the reverse. ψ is the **realized** discordance `(b+c)/180` of that very
contrast — never an assumed value. MDE is the effect resolvable at 80% power under
two-sided exact McNemar at α=0.05, integrated unconditionally over
`N_d ~ Binomial(n, ψ)`.

**Two-sided exact McNemar has no rejection region below n_d = 6.** Every contrast
below has n_d between 20 and 75, so **all of them are measurements**; none needs
the NOT A MEASUREMENT label. The paraphrase bank does preserve Track R's status as
an adequately-powered lane — better than preserve it. The original-bank Track R
lane ran ψ=0.10 with MDE 6.73pt; the paraphrase bank runs ψ=0.11–0.42 with MDE
7.1–13.8pt. Discordance rose (the bank is harder, so arms disagree more), which
*raises* the MDE in points; but every effect measured is far larger than its own
MDE, so no contrast here is power-limited.

### 4.1 MemPhant fused vs the scoped BM25 control — the ownership question

Control (attempt-scoped BM25, events of the bound attempt only): r@5 **0.1167**
(21/180), r@10 **0.2556** (46/180). On the original bank the same control scored
r@5 0.8278 / r@10 0.8944. Removing the lexical give-away costs the control
**0.64 of its recall** — the give-away was almost the whole of its performance.

| arm | stage | b | c | n_d | ψ | Δ | exact p | MDE@80% | power |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `bm25code_off` | fused@5 | 43 | 4 | 47 | 0.261 | +0.2167 | 2.78e-09 | 0.109 | 1.000 |
| `bm25code_off` | fused@10 | **48** | **5** | 53 | 0.294 | +0.2389 | 7.08e-10 | 0.116 | 1.000 |
| `bm25code_off` | packed@5 | 25 | 8 | 33 | 0.183 | +0.0944 | 0.00455 | 0.092 | 0.819 | **STALE**
| `bm25code_off` | packed@10 | 35 | 14 | 49 | 0.272 | +0.1167 | 0.00380 | 0.112 | 0.837 | **STALE**
| `bm25code_dense` | fused@5 | 61 | 4 | 65 | 0.361 | +0.3167 | 3.92e-14 | 0.129 | 1.000 |
| `bm25code_dense` | fused@10 | **71** | **4** | 75 | 0.417 | +0.3722 | 6.81e-17 | 0.138 | 1.000 |
| `bm25code_dense` | packed@5 | 43 | 6 | 49 | 0.272 | +0.2056 | 5.73e-08 | 0.112 | 1.000 | **STALE**
| `bm25code_dense` | packed@10 | 54 | 12 | 66 | 0.367 | +0.2333 | 1.69e-07 | 0.130 | 1.000 | **STALE**
| `overlap_dense` | fused@10 | 51 | 14 | 65 | 0.361 | +0.2056 | 4.48e-06 | 0.129 | 0.997 |
| `overlap_dense` | packed@10 | 37 | 23 | 60 | 0.333 | +0.0778 | 0.0925 | 0.124 | 0.394 | **STALE**
| `overlap_off` | fused@10 | 17 | 23 | 40 | 0.222 | −0.0333 | 0.430 | 0.101 | 0.122 |
| `overlap_off` | packed@10 | 12 | 27 | 39 | 0.217 | −0.0833 | 0.0237 | 0.100 | 0.626 | **STALE**

**Side-by-side, `bm25-code` (embeddings off) vs the control at fused@10:**

| | contaminated bank | decontaminated bank |
|---|---|---|
| b / c | 15 / 3 | **48 / 5** |
| exact p | 0.00754 | **7.08e-10** |
| margin over control | +0.0667 | **+0.2389** |
| at k=5, b / c | 24 / 2 | **43 / 4** |

**The ownership win survives decontamination and is roughly 3.6× larger.** The
survival ratio at fused@10 is 3.58. This is the opposite of the expected outcome
and it should be stated plainly: the prediction that the coding-lane win was
lexical give-away is **falsified**.

One honest qualification: the win grew largely because the *control collapsed*
(0.8944 → 0.2556), not because MemPhant improved — MemPhant also fell (0.9611 →
0.4944). Both fell; the control fell much further.

**No packed-stage survival claim is made here — retracted, for the second time.**
An earlier draft of this document reported that MemPhant "went from a 0.12 deficit
on the original bank to a +0.1167 advantage", i.e. a sign flip. That claim is
**withdrawn**, and it is worth recording that this program **already withdrew the
identical claim once before, on identical grounds**. It is wrong twice over:

- The paraphrase packed figures are pre-`f67f2b2a`; the original-bank packed
  figures they were compared against are post-fix. Two variables moved.
- The premise is false regardless. On trunk the original-bank packed figure is
  **0.9333 against the 0.8944 control = +0.0389, already a win**. There was never
  a deficit to flip out of.

This is not softened to a "trend" or a "direction". There is no packed-stage
finding in this document.

`overlap_off` — the pre-fix configuration — is now **losing to the control** at
packed@10 (b=12/c=27, p=0.0237). At fused@10 it is a genuine null (b=17/c=23,
n_d=40, p=0.430, power only 0.122 against its own −0.0333 effect, so this is
"no detectable difference", not "equivalent").

### 4.2 Attribution — where does the win come from?

| contrast | stage | b | c | n_d | ψ | Δ | exact p | power |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `bm25code_off` vs `overlap_off` | fused@10 | 52 | 3 | 55 | 0.306 | +0.2722 | 1.54e-12 | 1.000 |
| `bm25code_off` vs `overlap_off` | packed@10 | 41 | 5 | 46 | 0.256 | +0.2000 | 4.41e-08 | 1.000 | **STALE**
| `overlap_dense` vs `overlap_off` | fused@10 | 46 | 3 | 49 | 0.272 | +0.2389 | 6.98e-11 | 1.000 |
| `bm25code_dense` vs `bm25code_off` | fused@10 | **29** | **5** | 34 | 0.189 | +0.1333 | 3.86e-05 | 0.990 |
| `bm25code_dense` vs `bm25code_off` | packed@10 | 26 | 5 | 31 | 0.172 | +0.1167 | 1.92e-04 | 0.971 | **STALE**
| `bm25code_dense` vs `overlap_dense` | fused@10 | 43 | 6 | 49 | 0.272 | +0.1667 | 5.7e-08 | 1.000 |

The lexical fix and the dense fix are **both real and largely additive**: lexical
adds +0.2722 on top of overlap, dense adds +0.1333 on top of `bm25-code`, and the
two together take fused@10 from 0.2222 to 0.6278.

**The load-bearing decomposition is NOT re-tested.** The banked claim is finer
than "lexical helps": it is that plain textbook BM25 produced a *clean null* vs
the control (b=5/c=3, p=0.727 at k=10 — note n_d=8, barely above the n_d≥6
rejection floor, so that original null was already near-meaningless) and that the
**entire** win came from code-aware tokenization (`bm25-code` − `bm25-control` =
+0.0778 @5, +0.0555 @10). Separating those two requires a `bm25-control` arm on
the paraphrase bank, and **no such arm exists**. The paraphrase run has only a
two-level lexical ladder (`overlap` → `bm25-code`); the original had three
(`overlap` → `bm25-control` → `bm25-code`).

So: on clean data we can say the lexical change as a whole is worth +0.2722 at
fused@10. We **cannot** say how that splits between the BM25 algorithm and the
code-aware tokenizer. **That decomposition is currently unverified on any sound
instrument.** It should not be restated as banked until the arm runs. See §6.

### 4.3 Dense — the false null, confirmed

| | contaminated bank | decontaminated bank |
|---|---|---|
| dense on `bm25-code`, vs control @10 | b=17 / c=6, p=0.0347 | b=71 / c=4, p=6.8e-17 |
| dense on `bm25-code`, vs control @5 | b=24 / c=9, p=0.0135 | b=61 / c=4, p=3.9e-14 |
| dense **stacked**, vs `bm25-code` alone @10 | −10/+3 (declared dead) | **b=29 / c=5, p=3.86e-05** |
| dense on `overlap`, vs control @10 | b=16 / c=15, p=1.000 | b=51 / c=14, p=4.48e-06 |
| dense on `overlap`, vs control @5 | b=15 / c=24, p=0.200 | b=44 / c=13, p=4.71e-05 |
| best config | **no embeddings** | **`bm25-code` + `small`** |

Dense flips from null-or-negative to strongly positive on every comparison, at
power ≥ 0.99. This is exactly the regime the register predicted: a bank that
withholds 19.45 terms per query is where dense should express itself, and a bank
leaking 3.93× lexically is where it should not. **We abandoned a real direction on
a contaminated instrument.**

Note the runner has no "lexical off" mode (`--lexical-scorer` accepts only
`overlap`, `bm25-control`, `bm25-code`), so "dense alone" is operationalised as
`overlap` + `small`, matching how the original bank's `armC_dense_overlap` was
defined. Strictly-isolated dense is not measurable with this harness.

## 5. The packed ladder and miss composition — ALL LINEAGE-STALE (pre-`f67f2b2a`)

**Nothing in this section may be compared against a banked post-render-fix
figure, and no conclusion is drawn from it.** It is retained only to show what
the stale arms contain and to scope the re-run. Every number below carries the
`LINEAGE-STALE (pre-f67f2b2a)` stamp.

| arm | fused r@10 | packed r@10 | gold in pool | absent from pool | in-pool unpacked | budget | rerank | render |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `overlap_off` | 0.2222 | 0.1722 | 169 | 11 | 138 | 0 | 104 | 34 |
| `bm25code_off` | 0.4944 | 0.3722 | 169 | 11 | 102 | 3 | 69 | 30 |
| `overlap_dense` | 0.4611 | 0.3333 | 169 | 11 | 109 | 2 | 78 | 29 |
| `bm25code_dense` | **0.6278** | **0.4889** | 169 | 11 | 81 | 7 | 50 | 24 |

Stale decontaminated ladder in packed hits of 180: **31 → 60 → 67 → 88**.

**This must not be set beside the banked 91 → 113 → 139 → 168.** That ladder is
post-render-fix; this one is pre-. The apparent "magnitude collapses by roughly
half" is the sum of two changes — decontamination and a missing render fix — and
attributing it to the bank would be exactly the error §4.1 retracts. **No
magnitude claim is made.**

The fused ceilings *are* comparable and are the honest statement of what
decontamination costs: fused r@10 falls **0.9611 → 0.4944** (`bm25-code`) and the
pool ceiling is 169/180 against the original 173/180.

**The rerank observation is also LINEAGE-STALE and is downgraded to a
hypothesis.** The stale arms show the best configuration losing 50 gold to rerank,
24 to render and 7 to budget, against 3 / 30 / 4 on the original bank — rerank
apparently moving from negligible to dominant. It is a plausible mechanism (a
reranker riding the same lexical give-away the control was, demoting gold on
paraphrased queries), but `f67f2b2a` changes what renders and therefore changes
how misses are attributed across exactly these buckets. **It is not a finding, it
is a question for the re-run**, and it must not be cited as the coding lane's
largest recoverable pool until measured on trunk.

**Two upper rungs have no counterpart at all.** The packing-adjacency fix and the
`pack_render_cap` lever (the rungs that produced 168/180) were never run on the
paraphrase bank.

### 5.1 What a valid packed comparison requires

**Re-run the four paraphrase arms on trunk** (`accuracy-first`, which contains
both `f67f2b2a` and `3fc4eede`), against the same golden sha `4aed8e99…` and the
same corpus sha `c008142e…`, then re-score. Only then is a packed figure
comparable to the banked 168. Not run now — the host is saturated (§6). Recipe:

```bash
# in a worktree off accuracy-first, which HAS both render fixes
cargo build --release -p memphant-server -p memphant-worker -p memphant-cli
# then, per arm, varying --lexical-scorer {overlap,bm25-code} x --embed-model {off,small}
python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_paraphrase_golden.jsonl \
  --out-evidence  docs/build-log/artifacts/track-r-paraphrase/run/trunk-<arm>-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r-paraphrase/run/trunk-<arm>-provenance.json \
  --embed-model <off|small> --mode fast --k 10 --budget-tokens 8192 \
  --lexical-scorer <overlap|bm25-code> --label trunk-<arm> --port <unique> \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli
# stagger launches ~60s apart; each arm mints and drops its own scratch DB
python3 scripts/z1_paraphrase_ladder.py --run-dir <dir> --control <control> \
  --arm ... --contrast ... --out <artifact>
```

Fold the `bm25-control` attribution arm (§6) into the same sweep — it is the same
build and the same corpus ingest, so running five arms costs barely more than
four and closes both gaps at once.

## 6. What did not run, and why

**The `bm25-control` attribution arm was not executed.** The host was measured at
`loadavg 78.67 → 115.78` on `cpu_count = 12` (~9.6× oversubscribed) for the entire
window, with three `memphant-eval` arms from another lane and a Syndai mutation
suite in flight. A `cargo build --release` plus a full arm (ingest ~940s, compile
~2400s, recall ~1000s ≈ 1.2h on a quiet host) would both have been badly degraded
and would have degraded the other lanes' in-flight arms. Per the coordinator
advisory the build was gated on loadavg < 30; load **rose** rather than fell
across the window and the gate never opened.

This is a deferral, not a failure, and nothing here is reported as a defect
attributable to load. **Recipe, ready to run unchanged:**

```bash
cargo build --release -p memphant-server -p memphant-worker -p memphant-cli
python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_paraphrase_golden.jsonl \
  --out-evidence  docs/build-log/artifacts/track-r-paraphrase/run/par-bm25control-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r-paraphrase/run/par-bm25control-provenance.json \
  --embed-model off --mode fast --k 10 --budget-tokens 8192 \
  --lexical-scorer bm25-control --label par-bm25control --port 39620 \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli
```

Then re-run `scripts/z1_paraphrase_ladder.py` adding
`--arm bm25control_off=par-bm25control-provenance.json` and
`--contrast bm25code_off:bm25control_off --contrast bm25control_off:overlap_off`.

**Z2 needed no run — its premise is false.** Register §6.1 holds that the code
lane never banked packed evidence rows, so the Phase 3 paid-run ceiling is not
derivable. Two corrections:

1. `--emit-qa` is a **`memphant-eval bench-lme`** flag (the chat lane).
   `scripts/code_lane_run_memphant.py` has no such flag and never did; the code
   lane writes the same rows under **`--out-evidence`**. The Z2 recipe as written
   cannot be executed against the code-lane runner.
2. Those rows **already exist** for all four paraphrase arms and the control, and
   they are already in `run_reader.py`'s input schema. Verified:

   | evidence file | rows | unique qids | missing fields | empty evidence | mean items |
   |---|---:|---:|---:|---:|---:|
   | `before-overlap` | 180 | 180 | 0 | 9 | 9.50 |
   | `after-bm25code` | 180 | 180 | 0 | 9 | 9.50 |
   | `dense-overlap` | 180 | 180 | 0 | 9 | 9.50 |
   | `dense-bm25code` | 180 | 180 | 0 | 9 | 9.50 |
   | `bm25-scoped` (control) | 180 | 180 | 0 | 0 | 10.00 |

   Every row carries `question_id`, `question`, `question_type`,
   `is_abstention`, `gold_answer` and a ranked `evidence[]` of `{rank, body}` —
   exactly what `run_reader.py --evidence` consumes. The 9 empty-evidence rows
   per MemPhant arm are the questions where nothing was packed, which the reader
   scores as misses rather than choking on.

**The Phase 3 ceiling is therefore derivable today, at $0.** Reader accuracy on
the paraphrase bank is capped by packed r@10: **0.4889** for the best arm
(`bm25-code` + `small`), 0.3722 for `bm25-code` alone, against **0.2556** for the
control. Pricing a paid reader run does not need another retrieval run — only the
reader spend itself. The rows are gitignored bodies and stay uncommitted; they
live at `~/.memphant-private/track-r-paraphrase/run/`.

## 7. Banked claims to amend or withdraw

1. **WITHDRAW — "the best configuration uses no embeddings at all" / dense is
   dead for the coding lane.** Contradicted at p=3.86e-05, power 0.99. Dense adds
   +0.1333 fused@10 on top of `bm25-code` on a sound bank. The direction was
   abandoned on a contaminated instrument.
2. **AMEND — "the entire win came from code-aware tokenization."** Currently
   unverified on any sound instrument. Note also that the original null it rests
   on (b=5/c=3, n_d=8) was itself barely above the n_d≥6 rejection floor and
   should never have been read as evidence of no effect.
3. **AMEND, upward — the win over the scoped BM25 control.** It survives and
   grows: b=15/c=3 → b=48/c=5 at fused@10. Magnitude on the original bank was not
   a memory effect, but the *ordering* was right. State that the growth is driven
   mostly by the control's collapse (0.8944 → 0.2556).
4. **NO CHANGE — the packed ladder 91 → 113 → 139 → 168 stands unchallenged.**
   This pass produces **no valid decontaminated counterpart**. The stale
   31 → 60 → 67 → 88 is pre-`f67f2b2a` and must not be paired against it. Needs
   the trunk re-run in §5.1.
5. **WITHDRAWN BEFORE PUBLICATION (second occurrence) — the packed "sign flip",
   0.12 deficit → 0.12 advantage.** Wrong on lineage (stale vs post-fix arms) and
   wrong on premise (trunk original-bank packed is 0.9333 vs 0.8944 control =
   +0.0389, already a win — no deficit existed). **This program withdrew the
   identical claim once already, on identical grounds.** Recorded here so the
   record shows it was caught twice, not newly discovered.
6. **NOT A FINDING — the reranker as dominant loss channel** (50 vs 3 misses).
   Derived from packing-stage rows and therefore lineage-stale; `f67f2b2a`
   changes how misses are attributed across exactly these buckets. Downgraded to
   a hypothesis for the trunk re-run. Must not be cited as the coding lane's
   largest recoverable pool.
7. **CORRECT — register §4.2** says the paraphrase bank passes 19/20 bar checks;
   the lock records **20/21**. Same single failure.
8. **REGISTER DEFECT — §4.2 / §6.0 record the Z1 paraphrase arms as "verified as
   runnable but not executed". They were executed** on 2026-07-30 and committed
   as `af-w0-instrument` `3e2cc2ba`. The error propagated: the Z1 task brief
   repeated it verbatim and framed this work as "the first-ever execution".
   Fix the register entry so the next reader does not re-run banked arms.
9. **CORRECT — register §6.1's Z2 item.** The code lane's packed evidence rows
   are not missing; 180/180 reader-ready rows exist per arm under
   `--out-evidence`. The Phase 3 ceiling is derivable now (§6). `--emit-qa` is a
   chat-lane `bench-lme` flag and does not apply to the code-lane runner.

## 7.1 This is the case for A1 (lineage stamping) going first

**The same lineage trap produced the same false packed claim twice in two days,
in two independent sessions, both times inside careful prose that survived
review.** Neither session was careless: both stated their arms, both cited their
provenance, both reported exact cells. What neither could see from the artifact
alone was which commits the arms were built at, because **nothing in the
provenance report makes lineage comparable across artifacts** — `runtime_identity`
records a `git_head`, but reading it requires knowing which fixes to check
ancestry for, and the packed figure it qualifies carries no stamp at all.

A number that cannot be compared should not be readable as if it could. **A1
(lineage stamping) should go before any further packed-stage measurement**: until
each packed figure carries the render-lineage it was produced at, every
cross-artifact packed comparison in this program is one ancestry check away from
being wrong, and the check is not currently prompted by anything.

## 8. Artifacts

- `docs/build-log/artifacts/track-r-paraphrase/z1-ladder-power.json` — every arm,
  every paired 2×2 with `b`/`c`/`n_d`/realized ψ/MDE/power, drain block, miss
  composition. Schema `memphant.eval.track-r-z1-paraphrase-ladder.v1`.
- `scripts/z1_paraphrase_ladder.py` — the recompute. Validated: reproduces the
  committed W0.2 cells exactly (71/4, 48/5, 51/14, 17/23 at fused@10).

Golden bodies and the corpus are gitignored and are not committed here. Raw
per-arm provenance remains in the `af-w0-instrument` worktree and at
`~/.memphant-private/track-r-paraphrase/run/`.
