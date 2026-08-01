# S7 — MDN browser-compat-data: pinned, mined, and killed as an instrument

**Branch `s7-bcd` · 2026-08-01 · $0, `paid_model_calls: 0` · NOT merged, NOT pushed.**

---

## The answer to the four questions asked

**1. The pinned lock with verified licence.**
`benchmarks/manifests/browser_compat_data.lock.json`. Revision
**`9851c5cb2361b4fe35b6a49b4dbda64792579fd9`** (tag `v8.0.8` resolved through the
GitHub refs API — the tag is not trusted as an identity because tags move).
Licence **[F] CC0-1.0**: the LICENSE blob was **fetched at the pinned commit**
and read in full — verbatim *CC0 1.0 Universal*, 6,555 bytes, sha256
`36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673`. Corroborated
three ways, none of them a badge: the GitHub API licence object (`spdx_id
CC0-1.0`), the npm registry metadata, and — the load-bearing one — **the LICENSE
shipped inside the npm tarball hashes to the same sha256 as the blob fetched
from the pinned git commit**. Supply chain independently verified: recomputed
sha512 equals `dist.integrity`, recomputed sha1 equals `dist.shasum`.
`license_source: LICENSE_FILE`, never `unverified`.

**2. The mined bank and its yield against the ~75% false-positive gate.**
**326 features / 705 browser-pairs.** The gate is PAID: the naive
removed-then-added query returns **1,553** features / 5,079 pairs; the strict
filter (no `flags` / `prefix` / `alternative_name` / `partial_implementation`,
strict version gap) collapses it to 326 / 705 — a **79.0% false-positive rate**
against the survey's stated ~75%. Controls mined alongside: 211,911
never-removed and 1,965 stayed-removed browser-pairs.

**3. The trivial-baseline ceiling. 1.0000.**
`scoped_interval` — filter facts to the queried browser, take the greatest
version ≤ V, honour removals; about twenty lines, no substrate, no index, no
model call — scores **1.0000 on all 2,115 probes and on all three bands**.

**4. "Does this instrument discriminate, and at what n?"**
**No. And at no n.** Not because it is underpowered — 2,115 probes clear every
bar in the required-n table by 3× or more — but because the headroom above the
trivial baseline is **exactly 0.0000**. Against the saturating baseline
ψ = 0.0000 and `n_d` = 0, below the `n_d ≥ 6` floor at any sample size. **No n
rescues a zero effect ceiling.**

---

## What was measured

2,115 probes over 326 feature clusters. For each arc `(live_from L, removed_at
R, readded_at A)`, one probe per band: **B1** `L ≤ V < R` (supported), **B2**
`R ≤ V < A` (**not** supported), **B3** `V ≥ A` (supported). B2 discriminates:
there the newest assertion about the feature is the *wrong* answer.

| baseline | overall | B1 | **B2** | B3 |
|---|---:|---:|---:|---:|
| `constant_supported` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `most_frequent` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `latest_declared` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `max_version` | 0.6667 | 1.0000 | **0.0000** | 1.0000 |
| `max_version_le_V` | 1.0000 | 1.0000 | **1.0000** | 1.0000 |
| **`scoped_interval`** | **1.0000** | 1.0000 | **1.0000** | 1.0000 |

Robustness: re-run with **interior** query versions only, so the query is never
a declared version and no rule can win by exact match — 2,075 probes,
`scoped_interval` still **1.0000**, recency family 0.6704 with B2 still
**0.0000**. The saturation is not a sampling artifact.

**The good half, which must not be lost in the kill.** BCD does exactly what the
survey promised on the recency axis: constant, mode, latest-declared and
max-version **all score exactly 0.0000** on the discriminating band.
`max(timestamp)` here is not weak, it is *undefined* — currency is keyed to
scope and there is no timestamp axis. That is a strictly stronger property than
MemoryCode has. It is simply not sufficient.

---

## Why no rescue exists

BCD's gold **is** interval containment on a totally ordered `(browser, version)`
key, and the probe is indexed by that same key. Any probe whose gold derives
from BCD's intervals is by construction answerable by evaluating them. A
substrate could only beat the twenty-line rule if **our authored episode
wording** obscured the scope or version enough to make extraction hard — at
which point the difficulty is entirely authored by us, which is the self-mined
class this program discounts, and the thing measured is **extraction, not memory
semantics**. **On this corpus, "discriminating" and "inherited gold" are
mutually exclusive.** The property that made BCD attractive is the same property
that makes it trivially computable.

The executable endpoint of survey §4b.2 does not rescue it: §4b.7 correctly
gates it on the retrieval result, and the retrieval result is zero headroom.
Running a paid agent to show it can be handed the right fact by a twenty-line
rule measures nothing about a substrate. Not recommended, not requested, and
the calls were never authorised.

---

## The preregistration, and that it was obeyed

Written before any cell existed, kept verbatim at the top of the build log.

- **Trap 1 (level vs gap), preregistered**: a corpus that does not flatter
  recency makes **both** arms score worse; the endpoint is the **Δ between
  arms**, never either level; the predicted direction was **Δ grows**; both
  levels and Δ to be reported in one table beside the MemoryCode levels. Never
  exercised, because the run stopped at the ceiling screen — but recorded, so
  the next lane inherits it rather than rediscovering it.
- **Trap 2 (a different trivial rule), preregistered** with the **full** baseline
  set: `constant`, `most_frequent`, `latest_declared`, `max_version`,
  `max_version_le_V`, `scoped_interval`. S2 measured `most_frequent` beating
  `max(timestamp)` 70.7% to 55.5% on TempLAMA; S6 was saturated at 0.9064 by
  `max(observed_at ≤ t)`. **Trap 2 is what fired.**
- **Decision rule, preregistered**: *if a ~20-line read rule saturates the
  endpoint, the instrument has failed; say so and stop.* Obeyed exactly. No arm
  was built.
- **Prediction that was wrong, recorded as wrong**: `scoped_interval` was
  predicted "strong but beatable". It scored 1.0000. The reason is structural,
  not a tuning miss.

---

## Program consequence

| | S6 — MemoryCode as-of re-cut | S7 — MDN BCD |
|---|---|---|
| recency baseline broken? | **yes**, `max(observed_at)` → 0.0000 | **yes**, all four recency rules → 0.0000 on B2 |
| honest trivial rule | `max(observed_at ≤ t)` | `scoped_interval` |
| its score | **0.9064** | **1.0000** |
| re-assertions present? | **0** | **705** |
| verdict | saturated | **saturated harder** |

BCD **has** regime (a) — 705 genuine re-assertion arcs, the thing MemoryCode
structurally cannot supply — and it still saturates. **So the missing ingredient
is neither re-assertion nor non-recency currency.** Both are now present in a
pinned, CC0, externally-authored corpus and neither is sufficient. What is
needed is a corpus where resolving which fact is in force is **not computable
from the fact statements themselves** — where currency depends on something the
probe does not hand you.

**Standing recommendation:** measure the trivial ceiling **first**, at $0, before
any arm is built. That screen is now two-for-two at killing a week of engineering
in an afternoon.

**Nothing here says the banked +0.0301 or +0.0583 is wrong.** It says BCD cannot
be the instrument that tests them. No default, checkbox, cutover or SOTA claim
moves. **No paid spend is proposed.**

---

## Cost and hazards

$0. One 925 KB tarball, one 6.5 KB licence blob, two stdlib-only scripts running
in under a second each. **No server, no scratch database, no port, no embedding
model, no reader, no judge** — so no sibling lane was touched: nothing reaped,
no database dropped, no bootstrap lock queued. Avoided the survey's costed ~1 day
extraction + 3–4 days session synthesis + 2 days executable harness plus a
separately-authorised paid reader endpoint.

---

## Artifacts

| artifact | path |
|---|---|
| lock — pinned revision, verified CC0 licence, census, verdict | `benchmarks/manifests/browser_compat_data.lock.json` |
| mined arc bank, 705 rows | `benchmarks/manifests/browser_compat_data.arcs.json` |
| ceiling measurement + evidence contract (`decisional: false`, registered) | `docs/build-log/artifacts/2026-08-01-bcd-instrument/baseline-ceiling.json` |
| build log with the trap and prereg at the top | `docs/build-log/2026-08-01-bcd-instrument.md` |
| census + arc miner | `scripts/bcd_mine.py` |
| trivial-baseline ceiling | `scripts/bcd_baselines.py` |
| private mirror | `~/.memphant-private/w7-instruments/browser-compat-data` |

**Disclosed divergences from the survey, not tuned away:** the strict filter
returns 326 / 705 against the survey's 325 / 704 (one extra `nodejs` arc; the
survey's filter code was unavailable to diff), and the naive query reconstructs
to 1,553 rather than the survey's stated 1,279 (the survey does not specify it
precisely enough to reproduce). Neither can move a verdict that turns on a
ceiling of 1.0000.
