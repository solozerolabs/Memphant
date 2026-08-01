# MDN browser-compat-data as a discriminating coding instrument — pinned, mined, and killed

**Task S7 · branch `s7-bcd` · 2026-08-01 · $0, no paid call on any path.**

**Verdict up front: the instrument does not discriminate.** BCD breaks every
recency baseline exactly as promised — they score **0.0000** on the
discriminating band — and is then saturated at **1.0000** by a ~20-line rule
that touches no substrate. Headroom above the trivial baseline is **exactly
0.0000** on 2,115 probes. Do not build the session synthesiser, the probe
generator, or the executable harness costed in survey §4b.7.

The acquisition and the mining are sound and are kept: the lock
(`benchmarks/manifests/browser_compat_data.lock.json`), the verified CC0
licence, and the 705-row arc table
(`benchmarks/manifests/browser_compat_data.arcs.json`) all stand. What does not
stand is survey §4b's proposal that BCD can carry a retrieval decision about
supersession machinery.

---

## 0. THE TRAP, first, because it is the finding most likely to be misread

This section was written **before any cell existed** and is preserved verbatim.
It remains at the top even though the run never reached an arm, because the
reason it never reached an arm is the second trap, not the first.

**Trap 1 — level vs gap.** A corpus whose gold is not recency-identified will
make **BOTH arms score worse**. The tempting read is *"supersession regressed"*.
It will not have. The banked +0.0301 (bitemporal vs A-recency) and +0.0583
(Arm K derived keys) are measured on MemoryCode, whose gold is the latest
declaring session **by construction** — control, treatment and oracle all encode
the same correct rule, so the deltas are residuals between implementations of
one rule. Preregistered here, before the run:

1. **A DROP in absolute level, in both arms, is the EXPECTED outcome.** A level
   drop is not evidence about supersession and must not be reported as one.
2. **The endpoint is the GAP BETWEEN ARMS, never either level.**
3. **The predicted direction is written down before the run: Δ GROWS** relative
   to +0.0301, because the trivial control loses the free ride. If Δ shrinks or
   crosses zero, the machinery is not buying what is claimed for it.
4. **Report both levels and Δ in one table with the MemoryCode levels beside
   them**, so no reader can quote a level in isolation.

**Trap 2 — a corpus that defeats recency can still be defeated by a *different*
trivial rule.** S2 measured this on TempLAMA: `max(timestamp)` gets 55.5% while
`most_frequent_answer` gets **70.7%**. S6 then paid for it in full: its
MemoryCode as-of re-cut drove `max(observed_at)` to **exactly 0.0000** and was
still saturated by `max(observed_at <= t)` at **0.9064**.

**Trap 2 is what killed this lane, and it killed it harder than it killed S6.**
Where S6's trivial rule scored 0.9064, BCD's scores **1.0000**.

---

## 1. Preregistration — written before the first cell

### 1.1 Endpoint

For a probe scoped to `(feature, browser, version V)`, answer whether the
feature is supported. Gold is BCD's own interval containment. Primary decision
quantity would have been `Δ = accuracy(bitemporal) − accuracy(best trivial
baseline)` with a cluster bootstrap over BCD features. **Not either level.**

### 1.2 The full trivial-baseline set — all of it, not just A-recency

Preregistered at minimum, per S2 §3.3:

| baseline | rule |
|---|---|
| `constant_supported` | always answer "supported" — the constant/majority control |
| `most_frequent` | the modal answer over the bank — **the rule that beat recency on TempLAMA** |
| `latest_declared` | trust the last episode authored in the stream |
| `max_version` | the fact with the greatest version, query ignored — the `max(observed_at)` analogue |
| `max_version_le_V` | greatest version ≤ V, **blind to browser** |
| `scoped_interval` | filter to the queried browser, greatest version ≤ V, honour removals — **the S6-analogue honest rule, and the real bar** |

### 1.3 Decision rule, preregistered

**If a ~20-line read rule saturates the endpoint, the instrument has failed, and
the correct action is to say so and stop.** That is a complete result and it is
cheaper to find now than after four arms. Stated before the measurement; obeyed
after it.

### 1.4 Predicted direction, preregistered

`max_version` and `latest_declared` predicted to collapse on band B2 (they did —
to 0.0000). `scoped_interval` predicted to be *strong but beatable*, on the
reasoning that a memory substrate must first retrieve the right fact from an
unstructured stream. **That prediction was wrong**, and §4 explains why it was
wrong for a structural reason rather than a tuning reason.

---

## 2. Acquisition — pinned, with the licence fetched not guessed

| item | value |
|---|---|
| revision | **`9851c5cb2361b4fe35b6a49b4dbda64792579fd9`** — git tag `v8.0.8` resolved through the GitHub refs API to its commit sha. The tag is not trusted as an identity; tags move. |
| distribution | npm `@mdn/browser-compat-data@8.0.8`, published 2026-07-24T16:06:40.958Z |
| tarball | 925,167 bytes, sha256 `091e9a10…be07a81` |
| **supply chain** | recomputed sha512 equals the registry's `dist.integrity`; recomputed sha1 equals `dist.shasum`. **Both checked, both match.** |
| `data.json` | 19,891,093 bytes, sha256 `a1ff82ea…521a70f` |
| in-band self-ID | `data.json.__meta` reads `{"timestamp": "2026-07-24T16:06:32.098Z", "version": "8.0.8"}`, matching the registry publish time |
| mirror | `~/.memphant-private/w7-instruments/browser-compat-data` |
| lock | `benchmarks/manifests/browser_compat_data.lock.json` |

### 2.1 The licence, graded [F]

The **actual LICENSE blob was fetched at the pinned commit** — not `main`, not a
guessed path — and read in full: verbatim *CC0 1.0 Universal* text, 6,555 bytes,
sha256 `36ffd9dc…f39673`.

Three independent corroborations, **none of which is a badge**:

1. the GitHub API licence object reports `spdx_id: CC0-1.0` at path `LICENSE`;
2. the npm registry metadata for 8.0.8 reports `license: CC0-1.0`;
3. **the LICENSE shipped inside the npm tarball hashes to the *same* sha256 as
   the blob fetched from the pinned git commit** — the distributed artifact and
   the pinned source carry a byte-identical licence file.

No card-vs-file contradiction exists anywhere. This is the cleanest licence in
the S2 survey and the audit confirms it.

**One thing not to misread.** CC0 carries **no named rightsholder line**. That
is the licence working as designed — a waiver, not an attribution grant — and is
*categorically different* from the FreshQA / TAQA / HyTE failure of an
Apache-2.0 file shipped with the copyright line never filled in. Recorded so a
later reader does not file it under the same heading.

---

## 3. Mining — the census reproduces, and the §4b.4 false-positive gate is paid

`scripts/bcd_mine.py --census`, counted from the pinned `data.json`. **No survey
value, card value or README value was trusted.**

| quantity | measured | survey |
|---|---:|---:|
| compat features | **20,243** | 20,243 |
| support statements | **284,845** | 284,845 |
| bounded-validity statements (`version_added` **and** `version_removed`) | **7,454** | 7,454 |
| `status.deprecated` features | **1,152** | 1,152 |
| runtimes | **17** | 17 |

All five reproduce exactly. **They did not at first**: the initial pass returned
20,188 / 284,085 / 7,446 because it omitted the `manifests` (38) and
`mediatypes` (17) namespaces. That 55-feature gap is exactly those two. Recorded
because "close to the survey" is how a census silently drifts.

### 3.1 The false-positive gate, reproduced — and the yield stated

Survey §4b.4: the naive arc query is ~75% false positive; **any build reporting
a number between the naive and filtered counts has not applied the filter.**

| level | definition | features | browser-pairs |
|---|---|---:|---:|
| **L0** | any statement with `version_removed` + any statement with `version_added` | 1,624 | 5,398 |
| **L1 (naive)** | removed at R, added at A, both parse, **A ≥ R**. No modifier filter, no strict gap. `A == R` is the `AbortController`-in-Safari-12.1 trap: a `partial_implementation` upgraded to full support at the very version recorded as the removal | **1,553** | 5,079 |
| **L2 (strict, §4b.4)** | as L1, but neither statement carries `flags` / `prefix` / `alternative_name` / `partial_implementation`, and **A > R strictly** | **326** | **705** |

**Yield: 326 features / 705 browser-pairs. False-positive rate of the naive
query: 79.0%** against the survey's stated ~75%. **Gate PASSED.**

**Two divergences from the survey, disclosed rather than tuned away:**

- The filter returns **one more** feature and **one more** browser-pair than the
  survey (326/705 vs 325/704), and the extra one is in `nodejs` (71 vs 70). The
  survey's filter code was not available to diff, so the divergent case could
  not be identified. It cannot bear on the verdict: the verdict turns on a
  ceiling of 1.0000, which one arc cannot move.
- The survey states its naive query returns **1,279** features; the nearest
  reconstruction here returns **1,553**. The survey does not specify the naive
  query precisely enough to reproduce it. Both land an order of magnitude above
  326, which is the only property the gate depends on.

### 3.2 The canonical case, read from the data

`api.AbortSignal.timeout_static` in `nodejs`: `version_added` **16.14.0**,
`version_removed` **17.0.0**, re-added **17.3.0** — exactly as the survey
predicted, because the 17.x line branched before the backport. The Node arc set
is dense with the same LTS-backport shape (`api.Blob.*` 14.18.0→15.0.0→15.7.0,
the whole `PerformanceResourceTiming` surface 16.17.0→17.0.0→18.2.0,
`javascript.statements.import.import_attributes` 18.20.0→19.0.0→20.10.0).

### 3.3 The bank

| class | browser-pairs | regime |
|---|---:|---|
| **arc** | **705** (326 features) | (a) re-assertion |
| never_removed | 211,911 | control — never retired |
| stayed_removed | 1,965 | control — retired and never restored; the case where retire-and-forget is **correct** |

Only the 705-row arc table is committed
(`benchmarks/manifests/browser_compat_data.arcs.json`, sha256 `027f40e9…4764e7f`);
the controls are deterministic functions of the pinned `data.json` and are
regenerated by `scripts/bcd_mine.py --bank`.

### 3.4 What we author and what we inherit — stated, per §4b.4 gate 2

**We inherit the GOLD.** Which fact is in force in which `(browser, version)`
scope is BCD's annotation: externally maintained by MDN contributors, CC0,
machine-readable, revision-pinnable, and pinned here by sha256. That is a
strictly better evidence class than every coding bank this program owns, all of
which are self-mined gold.

**We would author the EPISODES.** BCD has no sessions and no natural-language
surface. **§4 is about why that asymmetry, which looked like a manageable
weakness, is in fact the thing that makes the instrument undecidable.**

---

## 4. The ceiling measurement — run BEFORE any arm, and it stops the lane

`scripts/bcd_baselines.py --report`. **2,115 probes over 326 feature clusters.**
For each arc `(L, R, A)`, one probe per band: **B1** `L ≤ V < R` (supported),
**B2** `R ≤ V < A` (**not** supported), **B3** `V ≥ A` (supported). **B2 is the
discriminating band** — there the newest assertion about the feature is the
*wrong* answer.

| baseline | overall | B1 | **B2** | B3 |
|---|---:|---:|---:|---:|
| `constant_supported` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `most_frequent` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `latest_declared` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `max_version` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `max_version_le_V` | 1.0000 | 1.0000 | **1.0000** | 1.0000 |
| **`scoped_interval`** | **1.0000** | 1.0000 | **1.0000** | 1.0000 |

On the cross-browser pool (a feature's Chrome, Opera and Node facts all in the
store) `max_version_le_V` drops to 0.9995 — scope-blindness costs something —
but `scoped_interval` stays at **1.0000**.

**Robustness.** Re-run with **interior** query versions only, so the query is
never a declared version and no rule can win by exact match: 2,075 probes,
`scoped_interval` **1.0000**, `max_version_le_V` **1.0000**, the recency family
0.6704 with B2 still **0.0000**. **The saturation is not a sampling artifact.**

### 4.1 The good half, which must not be lost in the kill

**BCD does what the survey promised on the recency axis.** Constant, mode,
latest-declared and max-version **all score exactly 0.0000** on the
discriminating band. `max(timestamp)` is not merely weak here, it is undefined —
currency is keyed to scope, and there is no timestamp axis to key it to. This is
a **strictly stronger property than MemoryCode has**, and it is real.

**It is simply not sufficient.** That is S6's lesson, reproduced on a second
corpus from a different domain, and it should now be treated as a law of this
program rather than a lesson: *breaking the recency baseline is necessary and
nowhere near sufficient, and the ceiling must be measured first, every time.*

### 4.2 Why the saturation is structural, not an authoring choice

The tempting rescue is "author harder episodes". It does not work, and the
reason is worth stating precisely because it generalises beyond BCD.

**BCD's gold *is* interval containment on a totally ordered `(browser, version)`
key, and the probe is indexed by that same key.** Any probe whose gold derives
from BCD's intervals is, by construction, answerable by evaluating those
intervals. A substrate can only beat the 20-line rule if **our authored episode
wording** obscures the scope or the version badly enough to make extraction
hard — at which point the difficulty is entirely authored by us, which is the
self-mined class this program already discounts, and the thing being measured is
**extraction, not memory semantics**.

**On this corpus, "discriminating" and "inherited gold" are mutually
exclusive.** The property that made BCD attractive — a clean, external,
machine-readable annotation of which fact is in force in which scope — is the
same property that makes it trivially computable.

### 4.3 The executable endpoint does not rescue it

Survey §4b.2 offers executable scoring — retire a live fact and the agent emits
a needless polyfill; keep a retired one and the code throws. That endpoint is
real, but §4b.7 correctly gates it on the retrieval result, **and the retrieval
result is zero headroom**. Running an agent to demonstrate that it can be handed
the right fact by a rule that costs 20 lines is not a measurement of a memory
substrate. It also requires paid calls that were never authorised. **Not
recommended, and not requested.**

---

## 5. Power — computed here, not inherited

`scripts/instrument_power.py`, two-sided exact conditional McNemar, α = 0.05,
80% power, `D_MIN = 0.07`, integrated unconditionally over `N_d ~ Binomial(n, ψ)`.

| ψ | 0.10 | 0.15 | 0.20 | 0.25 | 0.30 | 0.35 | 0.40 | 0.50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **required n** | 166 | 260 | 340 | 425 | 505 | 585 | 665 | 825 |

Recomputed, and it reproduces survey §5 exactly.

**The answer to "at what n does this discriminate" is: at no n.** BCD supplies
2,115 probes, clearing every bar in that table by 3× or more, so the instrument
was **never power-limited** — the survey was right that it is "not
power-limited; it is build-limited". It is **ceiling-limited**. Against the
honest trivial baseline the observed discordance is ψ = **0.0000** and
`n_d` = **0**, below the `n_d ≥ 6` floor at any n. **No sample size rescues a
zero effect ceiling.**

---

## 6. What this cost, and what it avoided

$0. One 925 KB tarball, one 6.5 KB licence blob, two stdlib-only scripts running
in under a second each. No server, no scratch database, no port, no embedding
model, no reader, no judge — **so none of the cross-lane hazards were touched**:
no process reaped, no database dropped, no bootstrap lock queued.

Avoided: the survey's costing of ~1 day extraction + 3–4 days session synthesis
+ 2 days executable harness, plus a separately-authorised paid reader endpoint.
**All of it, for a fraction of the extraction day**, because the ceiling was
measured before the build rather than after it.

---

## 7. Where this leaves the program

**The dataset gap is still real and BCD does not close it.** Two corpora have
now been taken to the same wall from opposite directions:

| | S6 — MemoryCode as-of re-cut | S7 — MDN BCD |
|---|---|---|
| recency baseline broken? | **yes**, `max(observed_at)` → 0.0000 | **yes**, all four recency rules → 0.0000 on B2 |
| honest trivial rule | `max(observed_at ≤ t)` | `scoped_interval` |
| its score | **0.9064** | **1.0000** |
| lineage | `Memphant-s6-asof:docs/build-log/2026-08-01-asof-recut.md:130` (read directly, not quoted from a brief) | `docs/build-log/artifacts/2026-08-01-bcd-instrument/baseline-ceiling.json` |
| re-assertions present? | **0** | **705** |
| verdict | saturated | **saturated harder** |

BCD *has* regime (a) — 705 genuine re-assertion arcs, the thing MemoryCode
structurally cannot supply — and it still saturates. **So the missing ingredient
is not re-assertion, and it is not non-recency currency.** Both are now present
in a pinned, CC0, externally-authored corpus, and neither is sufficient.

The missing ingredient is a corpus where **resolving which fact is in force is
not computable from the fact statements themselves** — where currency depends on
something the probe does not hand you. Every candidate that survives that test
should be screened by measuring its trivial ceiling **first**, at $0, before a
single arm is built. That screen is now two-for-two at killing a week of
engineering in an afternoon, and it should be the standing first step of any
instrument adoption.

**No paid spend is proposed. No further BCD work is proposed.**

---

## 8. Artifacts

| artifact | path |
|---|---|
| lock (pinned revision, verified CC0 licence, census, verdict) | `benchmarks/manifests/browser_compat_data.lock.json` |
| mined arc bank (705 rows) | `benchmarks/manifests/browser_compat_data.arcs.json` |
| ceiling measurement + evidence contract | `docs/build-log/artifacts/2026-08-01-bcd-instrument/baseline-ceiling.json` |
| census + arc miner | `scripts/bcd_mine.py` |
| trivial-baseline ceiling | `scripts/bcd_baselines.py` |
| private mirror | `~/.memphant-private/w7-instruments/browser-compat-data` |
