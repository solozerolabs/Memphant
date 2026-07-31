# Deleting the sibling-gather packing lever (2026-07-30)

`PackLevers::sibling_gather_enabled` is deleted. Once the W1 render-loss fix
(`chunk_completion_pass`, `docs/build-log/2026-07-30-packing-render-loss-fix.md`)
landed, the lever could no longer show the reader a byte the completion pass
misses, and its one remaining distinct behaviour was refilling an item past a
deliberate `pack_render_cap`.

## What was left of it

The completion pass already trades a partially chunk-rendered item up to FULL
chunk coverage — or to the bare whole body when the per-window headers do not
fit — whenever the pack's leftover budget covers the difference. That subsumed
the lever everywhere except one hypothesised residual band: the lever expands
INCREMENTALLY (one sibling chunk at a time), so in principle it could grow an
item on a pack too tight for the completion pass's all-or-nothing coverage.

Nobody had measured that band. This is that measurement.

## 1. The band is empty, by inequality

Admission renders each item at

```
render_budget = min(whole_cost, request_budget)      # lib.rs, packed_render
```

and `expand_siblings` retries EVERY unselected chunk (the radius loop walks all
indices, not just neighbours). So when admission returns, every unselected chunk
costs strictly more than `render_budget - charged`. Two cases, no third:

* **`whole_cost <= request_budget`** ⇒ `render_budget == whole_cost`. Any chunk
  the gather could add costs more than `whole_cost - charged` — so the leftover
  that would let the gather move is already enough for the completion pass's
  whole-body branch, which is full content coverage.
* **`whole_cost > request_budget`** ⇒ `render_budget == request_budget`, and the
  pack's leftover is at most `request_budget - charged`. No unselected chunk
  fits at all; the gather is a no-op.

Either way, an incremental sibling pass can never deliver content the completion
pass does not. The runnable form of that argument swept budgets `1..=260` over a
5-chunk unit plus a co-packed plain item, comparing lever-OFF against lever-ON
coverage at every budget, with vacuity guards so an all-covered or never-acting
sweep fails loudly:

| | |
|---|---|
| budgets leaving the item partially covered (lever off) | **39** |
| budgets where the lever changed bytes or cost | **50** |
| budgets where the lever added coverage | **0** |
| budgets where the lever removed coverage | **0** |

The lever moves bytes at 50 budgets and coverage at none of them.

## 2. Its one distinct behaviour was defeating `pack_render_cap`

Under a `pack_render_cap` the completion pass is deliberately disabled —
bounding the item is that lever's entire purpose (rung 7,
`docs/build-log/2026-07-21-rung7-packing-diagnosis.md`). `sibling_gather_pass`
had no such guard and refilled from the pack's leftover budget anyway:

```
cap = 40 tokens
  cap only        → 1 chunk  @  26 tokens
  cap + gather    → 5 chunks @ 124 tokens   (3.1× the cap)
```

So the only band where the lever acted alone is one where it undid a lever with
a measured win.

## 3. LME-S: no retrieval movement, net content loss

Paired arms on the frozen dev cohort. Deterministic, no reader, no paid call;
scratch DBs minted and dropped per arm by `scripts/with_scratch_db.sh`.

```
n=178  seed=20260710  k=10  budget=8192  pool=64  embed=small  mode=fast
```

| | OFF | ON |
|---|---|---|
| recall@5 | 0.6144578 | 0.6144578 |
| recall@10 | 0.6144578 | 0.6144578 |
| paired flips @5 / @10 | — | 0 before-only, 0 after-only (McNemar p = 1.0) |
| per-question vector | — | identical |
| packed items | 778 | 778 |

The retrieval null is expected and was predicted before the run: the pass runs
strictly AFTER the greedy fill and never changes which candidates are admitted,
so recall@k cannot move. It is a confirmation, not the finding.

The finding is the pack content. Episode UUIDs are minted fresh per scratch DB,
so 411 items differ by id alone; all counts below are on id-normalized bodies.

| | |
|---|---|
| items identical | 689 / 778 |
| items ON rendered SHORTER | **82** |
| items ON rendered LONGER | 7 |
| net content delta (ON − OFF) | **−52,946 chars** |
| items where OFF showed content ON lacked | **16** |
| items where ON showed content OFF lacked | 2 |
| items with interleaved (neither-contains) content | 60 |

Both ON gains are pack-level budget-contention artifacts, not band hits: the
gather spends leftover budget on one item, which changes which OTHER item the
completion pass can afford. One is worth +24 chars to its pack. Against that,
the lever costs the cohort ~53k characters of packed content for zero retrieval
movement.

## 4. It had no production surface

`with_sibling_gather_enabled` had exactly ONE call site in the entire tree
(`crates/memphant-eval/src/bench_lme.rs`). Nothing in `memphant-server`,
`memphant-cli`, `memphant-worker`, or any script referenced it. The lever was
unreachable from the served path, so its deletion changes no deployed behaviour
— and the Track R coding lane, which routes through the server, could not have
measured it without new plumbing built solely to justify the thing being
measured.

## What was deleted

* `crates/memphant-core/src/lib.rs` — `PackLevers::sibling_gather_enabled`,
  `struct ChunkSiblings`, `PackCtx::sibling_gather_enabled`,
  `PackAccumulator::sibling_masks` (and its `evict` line), the sibling capture
  in `admit_new`, `fn sibling_gather_pass`, and its call site.
* `crates/memphant-core/src/service.rs` — `with_sibling_gather_enabled`.
* `crates/memphant-eval` — the `--sibling-gather` flag, its usage entry, and the
  `Options` / `BenchLmeReport` fields.
* `scripts/compare_lme_split_recall.py` — the `sibling_gather` pinned setting.

**Kept:** `expand_siblings`. `select_chunk_mask` still uses it for
admission-time ±1 sibling expansion; only the post-pass caller went away.

The lever test `sibling_gather_expands_item_without_eviction_or_overbudget` is
replaced by `partial_render_reaches_full_coverage_without_eviction_or_overbudget`,
which keeps every invariant that was worth having (no eviction, never over
budget, co-packed item untouched, window headers intact) and gets them from the
completion pass instead.

## Backward compatibility

Roughly 50 frozen artifacts under `docs/build-log/artifacts/` carry
`"sibling_gather": <bool>`, including the lever-ON run
`artifacts/wave-20260711/lme-wave-sibling.json`. `BenchLmeReport` has no
`deny_unknown_fields`, so they all still parse with the field gone; the
legacy-report test now asserts that directly rather than trusting it.

`compare_lme_split_recall.py` had `sibling_gather` in `PINNED_SETTINGS`. It had
to go: post-deletion reports omit the field while frozen ones carry `false`, so
pinning it would fail every old-vs-new comparison on a difference that no longer
means anything. A comment there says so, to stop it being re-added.

## Reproduction

```bash
cargo test -p memphant-core --lib
cargo test -p memphant-eval

# The two paired LME-S arms (~40 min each, $0, no reader). The ON arm requires
# reverting this commit — the flag no longer exists.
cargo build --release -p memphant-eval --features fastembed
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant LME_DB \
  sh -c 'target/release/memphant-eval bench-lme --database-url "$LME_DB" \
    --data benchmarks/data/longmemeval_s.development.json \
    --sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64 \
    --embed-model small --emit-qa <arm>-evidence.jsonl --out <arm>-retrieval.json'

PYTHONPATH=scripts python3 scripts/analyze_lme_pack_nonregression.py \
  --before off-retrieval.json --after on-retrieval.json \
  --before-evidence off-evidence.jsonl --after-evidence on-evidence.jsonl \
  --out docs/build-log/artifacts/sibling-gather-deletion/lme-s-nonregression.json
```

Committed artifacts:
`docs/build-log/artifacts/sibling-gather-deletion/sibling_gather_deletion.json`
(the full verdict record) and `.../lme-s-nonregression.json` (the paired
analyzer output). The per-question evidence JSONLs are not committed — they
carry third-party session bodies, same rule as `phase1w/`.
