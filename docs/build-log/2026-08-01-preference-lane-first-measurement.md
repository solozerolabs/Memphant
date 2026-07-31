# Preference lane — first measurement. A clear negative, and the reason why.

**Date:** 2026-08-01 · **Branch:** `af-w8-memcode` · **Prereg:** `docs/build-log/2026-08-01-preference-lane-prereg.md` (committed `968d7fba`, before any arm ran)
**Cost:** $0. Regex/rule-derived gold only. `paid_model_calls: 0` in both artifacts.

## Verdict in one line

On 1063 externally-authored supersession probes, **MemPhant does not beat a BM25
lexical baseline at telling a live rule from a retired one** — and both arms
apply the retired rule roughly **two times in three**.

## 1. Primary result

**Endpoint: latest-state-wins (LSW)** — when a coding convention has been
superseded, does the *current* session outrank the retired one?

| | MemPhant (Arm A) | BM25 lexical control (Arm B) |
|---|---|---|
| **Latest-state-wins / Appropriate Application Rate** | **0.3123** [0.2846, 0.3414] | **0.3198** [0.2901, 0.3502] |
| **Misapplication Rate** | **0.6717** [0.6423, 0.6994] | **0.6736** [0.6431, 0.7035] |
| Neither returned | 0.0103 [0.0040, 0.0174] | 0.0019 [0.0000, 0.0048] |

Brackets are **cluster-bootstrap 95% CIs over the 257 instances**, 10,000
resamples, seed `20260801`.

**ΔLSW (A − B) = −0.0075**, cluster-bootstrap 95% CI **[−0.0370, +0.0228]**.

| test | value |
|---|---|
| cluster permutation (instance-level label flip, 10,000 perms) | **p = 0.657** |
| exact McNemar (two-sided binomial on 134 A-only / 142 B-only discordant probes) | **p = 0.674** |

The two p-values agree here, so nothing is hiding in the clustering on this
endpoint — but the McNemar remains the anti-conservative reference by prereg,
and the bootstrap CI is the verdict.

**By the preregistered decision rule this is a NEGATIVE.** The CI includes 0.
MemPhant's point estimate is *below* the lexical control. Stated as the prereg
required it to be stated: *MemPhant cannot beat a lexical baseline at telling a
live rule from a dead one, and that is the finding.*

**The three buckets sum to 1 in both arms**, so no suppression win is
masquerading as an application win. The one endpoint where the arms differ
significantly runs **against** MemPhant: it returns *neither* session more often
(Δ +0.85pp, cluster-permutation p = 0.029).

**Secondary, descriptive (no test).** MemPhant is also worse at simply surfacing
the current rule at all: `hit@1` 0.231 vs 0.268, `hit@10` **0.786 vs 0.933**.
That is a retrieval-coverage deficit sitting *on top of* the staleness deficit.

**H2 (descriptive).** The adoption smoke's MR of 15/26 = 0.577 **understated**
the failure. At n = 1063 the Misapplication Rate is **0.672**.

## 2. The diagnosis — worth more than the score

Read from the run's own scratch DB before it was dropped
(`arm-a-memphant.json` → `diagnostics`), on the bench superuser credential:

| query | result |
|---|---|
| `memory_edge` by kind | **`[]` — zero edges of any kind** |
| `memory_unit` by state | **`active` = 8147** (all of them) |
| `memory_unit` by kind | **`episodic` = 8147** (all of them) |
| units with a `predicate` | **0 of 8147** |
| units with a `fact_key` | 8147 of 8147 — every one **auto-derived** (content hash) |
| superseded units with open `transaction_to` | 0 |
| `episode.retention_tier` | **`hot` = 8147** |
| jobs remaining / failed | 0 / 0 |

### Supersession is not broken. It is unreachable.

Nothing on the default ingest path can ever mint a `Supersedes` edge or move a
unit to `Superseded`. The chain, verified in source:

1. `scripts/gate_runtime.py:episode_retain_payload` — the strict public retain
   shape — carries no subject/predicate. Correctly so: they are compiler hints.
2. `crates/memphant-core/src/service.rs:5305` (`compile_job`) emits **exactly one**
   `ReflectCandidate` per episode: `fact_key: None`, `subject: None`,
   `predicate: None`, `kind: Some(MemoryKind::Episodic)`. Fact extraction is
   **default OFF** (`service.rs:3402`) and there is no structured provider at $0.
3. `crates/memphant-core/src/lib.rs:12222` (`has_explicit_subject`) therefore
   returns **false**.
4. `lib.rs:11670` gates the entire supersession branch on `if explicit_subject`,
   under its own comment: *"AUTO-KEYS NEVER SUPERSEDE"*. **Never entered.**
5. Second lock: even if entered, its candidate filter requires
   `existing.kind == MemoryKind::Semantic`. These units are `Episodic`.

So the `UnitState::Superseded` recall exclusion the brief pointed at is not
failing — **it is never asked to run.** `unit_is_recallable` (`lib.rs:10146`)
admits `Superseded && transaction_to.is_some()` and `bitemporally_recallable`
then closes it for a live `transaction_as_of`; that logic is correct and dead.
Nothing ever reaches state `superseded`.

### And there is no fallback recency signal in the ranker.

Two independent mechanisms could have broken a stale-vs-current tie. Neither
fires:

- **Temporal channel.** `temporal_score` (`lib.rs:10896`) returns 1.0 only when
  the query literally contains the token `current`, `latest`, or `now` **AND**
  the unit is `Semantic` + `Active`. MemoryCode probe queries are convention
  statements, and every unit here is `Episodic`. The temporal channel therefore
  contributes **0.0 for all 8147 units on all 1063 probes**.
- **Decay.** `days_since_last_review` (`lib.rs:11062`) returns a **constant
  `14.0`** for any unit with zero review events. This bench issues no `mark`, so
  every unit receives an *identical* retrievability, and
  `candidate.fused_score *= candidate.decay.retrievability` (`lib.rs:7364`) is an
  order-preserving global scalar. **Decay contributes exactly nothing.** Decay
  keys on review events, never on wall-clock age — even though `observed_at` is
  stored and correct here (the adapter spaced sessions one minute apart in true
  chronological order, so the recency information *was* in the data).

**Net:** on this construct MemPhant's ranking reduces to semantic + lexical
fusion with no state and no time. It is the same *kind* of ranker as BM25, so it
scores like BM25. The measurement and the mechanism agree.

**Trace shape of the modal failure**, from `arm-a-memphant.json` rows: the
superseded session at rank 0 and the current session immediately behind it at
rank 1 — e.g. `mc-61-9114dd9bed7e`, `mc-67-9c7d6a9d3b60`,
`mc-69-4241f525e07e`. Both are retrieved. The system has the right document and
puts the wrong one first. This is not a recall failure; it is the total absence
of a live/dead distinction.

## 3. What this implies

### For the typed write-router (`04` §13, SPECCED-UNBUILT)

**The bottleneck is the write path, not the read path.** Until something mints
an explicit subject key for a restated convention, no read-side change —
reranker, larger budget, deeper pool, better embedder — can separate a live rule
from a retired one, because the two units are the same kind, the same state, the
same tier, carry the same (constant) decay, and differ only in their text. A
reranker asked to choose between them is being asked to guess.

This is direct evidence for the spec's own decision in **§13.2a**, which says a
`preference`'s retrieval default is *deterministic hot-plane injection of the
chain head — not competitive retrieval; the one kind whose recall path is
assembly, not ranking.* Competitive retrieval on this construct lands at 0.312
with a ±3pp CI and is statistically indistinguishable from BM25. **Ranking is
not the lever. Assembly is.** §13.2a also specifies a preference has *no decay*;
this run shows decay is already inert here by accident, and that its inertness
is not helping — the gap is not "tune decay", it is "there is no chain head to
inject".

Concretely, the router arm needs to produce, for a restated convention: an
explicit `(subject, predicate)`, a non-`Episodic` kind, and the supersession
edge that follows. All three are absent today, and each one alone is sufficient
to block the result.

### For the absent hot/cold plane

`retention_tier` measured **`hot` = 8147**, confirming empirically the 0-readers
/ 0-writers finding. But the honest scoping matters: **a tiering job would not
have moved this number.** Demotion changes where a memory is stored; it does not
mark a rule retired. Conflating storage tier with unit state is exactly the trap
`04` §13.4 and §939 warn about. On this evidence the absent plane is a **cost**
story, not an accuracy story — **this run gives no evidence that tiering would
move latest-state-wins**, and it should not be cited as if it did.

### For the lane

Track U's 51 goldens (`~/.memphant-private/track-u/user_lane_golden.jsonl`)
remain unmeasured; this run scores the lane's *construct* through an external,
deterministically-graded instrument instead. The lane now has a number where it
had none: **0.312 latest-state-wins, 0.672 misapplication, at parity with BM25.**

## 4. Deviations from the prereg

1. **Merged `accuracy-first` mid-task and discarded a partial Arm A.** The
   worktree carried migration `20260730_004` (which put the worker pool under
   FORCE RLS, so its queue-wide count matched zero rows and
   `MEMPHANT_WORKER_DRAIN=1` reported one batch as a completed drain) **without**
   the `20260730_005_pending_worker_job_count.sql` fix. An Arm A run that was
   mid-ingest was **killed and its artifacts deleted** rather than reconciled;
   both arms were re-run from scratch on the merged tree (`3eedb5c5`). No probe
   from the discarded run appears in any reported number.
2. **Added a corpus-compiled gate to the adapter** (`verify_corpus_compiled`),
   which asserts on the bench superuser credential — never a worker self-report —
   that 0 jobs are queued/running, 0 failed/dead, the episode count matches what
   retain accepted, and **every episode minted at least one unit**. It passed:
   `{"episodes": 8147, "episodes_with_units": 8147, "expected_episodes": 8147,
   "failed_jobs": 0, "memory_units": 8147, "pending_jobs": 0}`. This is a
   strengthening, not a loosening — a partially compiled corpus would have
   manufactured exactly this report's headline.
3. Arm B was re-run post-merge for cleanliness; it is DB-free and produced
   byte-identical summary figures.

No endpoint, arm, filter, exclusion, or analysis choice was changed after any
result was seen. No arm was re-run on an unfavourable result.

## 5. Reproduce

```bash
cd /Users/sidsharma/Memphant-af-w8-memcode      # branch af-w8-memcode @ bdeeb834
docker start memphant-postgres-1
cargo build --release --bin memphant-server --bin memphant-worker --bin memphant-cli
OUT=docs/build-log/artifacts/2026-08-01-preference-lane
SRC=~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet

# Arm B — lexical control. No DB, no server, no network. ~9 s.
<venv-with-pyarrow>/bin/python scripts/external_instrument_adapter.py \
  --instrument memorycode --arm lexical --source $SRC --out $OUT/arm-b-lexical.json

# Arm A — MemPhant. Self-re-execs onto a fresh scratch DB, drops it on exit. ~27 min wall.
<venv-with-pyarrow>/bin/python scripts/external_instrument_adapter.py \
  --instrument memorycode --arm memphant --diagnostics --source $SRC \
  --out $OUT/arm-a-memphant.json --port 39485

# Preregistered analysis: cluster bootstrap + exact McNemar + cluster permutation.
<venv-with-pyarrow>/bin/python scripts/preference_lane_analysis.py \
  --arm-a $OUT/arm-a-memphant.json --arm-b $OUT/arm-b-lexical.json --out $OUT/analysis.json
```

`pyarrow` is the only extra dependency (MemoryCode ships parquet). The adapter
verifies the pinned sha256 `1edb1238…` before any database is minted and refuses
to run if the mirror mutated.

## 6. Artifacts and commits

| artifact | contents |
|---|---|
| `docs/build-log/artifacts/2026-08-01-preference-lane/probe-bank-count.json` | bank recounted pre-registration; 811/8400 `session_regex` misalignment reproduced |
| `docs/build-log/artifacts/2026-08-01-preference-lane/arm-a-memphant.json` | Arm A, 1063 rows, diagnostics, compilation gate |
| `docs/build-log/artifacts/2026-08-01-preference-lane/arm-b-lexical.json` | Arm B, 1063 rows |
| `docs/build-log/artifacts/2026-08-01-preference-lane/analysis.json` | three endpoints × {rates, CIs, McNemar, cluster permutation} |

| commit | what |
|---|---|
| `968d7fba` | prereg + probe-bank count, committed before any measurement |
| `3eedb5c5` | merge `accuracy-first` (brings `20260730_005`) |
| `bdeeb834` | lexical arm, directional rates, corpus-compiled gate |

Corpus pinned: `CohereLabsCommunity/memorycode` rev
`32d888b11c73c67be91414e571dfe98c5c20feac`, Apache-2.0, sha256
`1edb12380ea3410c888fffa795f6ddd3251e4e634b84a7142c8386e7c2869733`.

**Nothing was run for money. No checkbox, default, cutover, or SOTA claim moves.**
