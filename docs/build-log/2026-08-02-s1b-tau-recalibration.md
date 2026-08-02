# S1b — τ re-calibration against the sentence distribution

Lane S1b. Preregistered in `docs/build-log/2026-08-01-similarity-unit-swap.md` §10.
Offline, DB-free, no server, no model, no network. **$0, zero paid model calls.**

Artifact: `docs/build-log/artifacts/2026-08-02-s1b-tau/tau-sweep.json`
Script: `scripts/s1b_tau_sweep.py`
Inputs: banked `arm-u-sentence.json` ledger (7,890 candidate pairs) + pinned
MemoryCode parquet (sha256 verified against `benchmarks/manifests/memorycode.lock.json`).

## What S1 asked for

Two constraints were preregistered:

1. Report the **realized firing count** beside every candidate τ — the offline
   precision figures (0.764 at 1,021 firings vs 0.556 at 2,000) were lumpy, so
   the plateau structure had to be shown, not smoothed.
2. Watch `neither_returned`.

Constraint 1 is discharged below. Constraint 2 is a live-arm endpoint and is
carried to the live decision at the bottom; nothing offline can report it.

## The structure nobody had looked at

The sentence unit is **quantized**. Across 7,890 pairs it takes only **69
distinct values**, and they are small-denominator fractions — 0 (5,429 pairs),
1/2 (270), 1 (252), 1/3 (201), 2/5 (164), 3/4 (161). A τ sweep is therefore not
a curve, it is a **staircase with 68 reachable operating points**. Every τ
strictly between two adjacent values is the same arm.

That alone kills the framing S1 §10 used. There is no τ to tune; there is a
short list of arms to choose from.

## The sweep

7,890 pairs scored, **1,122 co-declaring** (the gold positive set for this
ledger; the earlier B1-ledger rescoring counted 1,174 on a different pair set).

Selected reachable points — full 68-row table in the artifact:

| τ | firings | precision | recall | F1 |
|---:|---:|---:|---:|---:|
| 0.153846 | 2048 | 0.5215 | 0.9519 | 0.674 |
| 0.250000 | 1701 | 0.5855 | 0.8877 | 0.706 |
| 0.333333 | 1452 | 0.6288 | 0.8137 | 0.709 |
| 0.400000 | 1186 | 0.6813 | 0.7201 | 0.700 |
| **0.416667** | **1022** | **0.7642** | **0.6961** | **0.7286** |
| **0.428571 (U, τ=0.42)** | **1021** | **0.7640** | **0.6952** | **0.7280** |
| 0.444444 | 940 | 0.7787 | 0.6524 | 0.710 |
| 0.500000 | 924 | 0.7771 | 0.6399 | 0.702 |
| **0.545455** | **654** | **0.9113** | **0.5312** | **0.671** |
| 0.600000 | 612 | 0.9167 | 0.5000 | 0.647 |
| 0.714286 | 463 | 0.9741 | 0.4020 | 0.569 |
| 1.000000 | 252 | 0.9762 | 0.2193 | 0.358 |

Arm U as run: 1,021 firings, precision 0.763957, recall 0.695187.

## Verdict — the sweep is NEGATIVE

**τ = 0.42 already sits on the F1 optimum of the reachable staircase.** The
best reachable point is 0.416667 at F1 0.7286 versus U's 0.7280 — a difference
of **one pair out of 7,890**. There is no re-calibration to buy. S1 §10's
remaining free move is spent, and it returns nothing.

This is worth stating plainly because the offline precision census that
motivated S1b (0.765 at 1,091 → 0.556 at 2,000) reads as "we are operating too
loose". We are not. That decay is entirely below τ=0.42; above it the curve is
flat until 0.5 and then cliffs.

## The one thing the sweep did surface

Between τ=0.5 and τ=0.545455 the curve is not smooth. Precision steps
**0.7771 → 0.9113** while firings drop **924 → 654**. It is the only sharp
move on the staircase, and it is the only remaining candidate arm on rung one:

> **τ = 0.53** — same unit, same pipeline, +0.147 precision, −367 edges.

Whether that is a win is genuinely unknown and cannot be settled offline:

- **For it.** Arm K's redirecting finding was that K's hit@k *fell* while
  oracle P's *rose* — the cost of keying wrongly, not of keying. A 0.911-
  precision edge set is the direct expression of that lever.
- **Against it.** U's retrieval tax is already −0.0151 [−0.0350, +0.0047],
  indistinguishable from zero, so there is little tax left to recover, and
  −36% of the edges is a real coverage loss against latest-state-wins.

The prior is weakly negative. The endpoint that decides it is LSW, with
`neither_returned` watched per S1 constraint 2.

**Cost to settle: one live arm, $0, ~50 min wall — plus a same-tree rebuild and
a re-run of the τ=0.42 reference, because the banked U artifact was produced by
a binary from a tree that no longer exists as a worktree.** Same pipeline stage,
same haystack: the pair must be built and run together or it is not a pair.

## Reproduce

```
uv venv .v && uv pip install -p .v/bin/python pyarrow
.v/bin/python scripts/s1b_tau_sweep.py \
  --arm-u docs/build-log/artifacts/2026-08-01-similarity-unit-swap/arm-u-sentence.json \
  --source ~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet \
  --out docs/build-log/artifacts/2026-08-02-s1b-tau/tau-sweep.json
```

Seconds, not minutes. `pyarrow` is the only dependency.
