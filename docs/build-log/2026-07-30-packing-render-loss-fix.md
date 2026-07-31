# Packing: a partially chunk-rendered item may now emit its whole body (2026-07-30)

Cost: **$0**. No reader, no judge, no paid model call. Every figure below comes
from an executed run with a named artifact. No checkbox, default, cutover,
deployment, or SOTA claim moves in this document.

Closes the defect `docs/build-log/2026-07-30-packing-rank-order-fix.md` §6
recorded and deliberately did **not** fix, and the last large packing loss named
in `docs/build-log/2026-07-30-coding-lane-first-win.md` ("What remains — 34
questions, and 30 of them are one defect").

## Headline

| lane | metric | before | after |
|---|---|---:|---:|
| **coding** (Track R, 180) | packed r@10 | 0.7722 (139) | **0.9333 (168)** |
| | packed r@5 | 0.7278 (131) | **0.9222 (166)** |
| | fused top-10 ceiling | 173 | 173 (unchanged) |
| **chat** (LME-S, 178) | r@5 / r@10 | 0.6145 / 0.6145 | **0.6145 / 0.6145** |

- Coding lane: **29 gains, 0 losses** at k=10, McNemar exact **p = 3.73e-09**;
  35/0 at k=5, **p = 5.82e-11**. **29 of the 30 render losses recovered.**
- Chat lane: **0 flips in either direction** on all 166 scored questions,
  p = 1.0, per-question hit vectors identical — while the packed context is
  **not** byte-identical (the pass fires there too). Not inert, not a
  regression.
- **Packed-item counts are identical on both lanes**, arm for arm. Nothing was
  displaced anywhere. That is the property the reverted patch could not have.

## 1. The mechanism, with a trace

`packed_render` (`crates/memphant-core/src/lib.rs`) gives an item a render
budget of `whole_body_tokens.min(request_budget_tokens)` and then renders from
contextual chunks. Each chunk *block* is charged
`conservative_token_estimate(header + "\n" + body) + 1` — its provenance header
**on top of** its body. Chunk bodies are byte slices of the unit body
(`episode_contextual_chunks` / `resource_contextual_chunks` both emit
`body[start..end]`, and together they cover it), so

```
Σ chunk_block_token_cost  >  whole_body_tokens  =  the render budget
```

*always*. Full chunk coverage never fits its own budget. An uncapped chunked
item is therefore structurally unable to emit all of itself: it drops chunks
while charging nearly the whole-body price, and the span the reader needs goes
with the dropped chunks.

**Trace, `track_r_021`** (baseline arm, per-question artifact). Gold at fused
rank **1**, gold unit **in packed slot 0**, question still a miss:

| | before | after |
|---|---|---|
| slot-0 body | 578 chars, begins `[episode 019fb52c-…] [kind tool] [segments 1-4]` | 696 chars, begins `assistant: Let me check how to properly set the configuration option:` |
| render | one chunk block, segments 1–4 only | whole body |
| `hit_at_10` | false | **true** |

The after-body contains the before-body's chunk text verbatim (minus the header
line) plus the rest of the unit — the superset property, observed rather than
assumed.

Run-level, baseline arm: 37 in-pool-unpacked misses, of which
`in_pool_unpacked_with_gold_unit_packed` = **30**. Those 30 are the render
losses; `budget_share_of_in_pool_unpacked` = 0.108, and `packed_size` is 10 on
176/180 questions — the coding lane is **slot**-bound, not budget-bound.

## 2. The fix, and why it is not the reverted patch

`f67f2b2a`, one post-fill pass — the twin of `sibling_gather_pass`:

> An item rendered from a **partial** chunk selection takes its whole body when
> the pack's **leftover** budget covers the difference
> (`whole_body_tokens − already_charged ≥ 0`).

The reverted patch was an **admission-time** fallback. It raised the per-item
cost the greedy fill charges, so on a budget-bound pack it displaced whole items
— pointed straight at the chat lane's 64/64 `Budget` drops — and it pre-empted
`sibling_gather`, whose entire purpose is to re-expand a partially rendered item
after the fill.

This one runs **after** the fill, so:

1. **Admission is untouched.** The packed item set, the order, and every drop
   record are byte-identical to before. A budget-bound pack has zero leftover
   and the pass is a no-op; a slot-bound pack recovers full coverage. The
   measured witness is that `packed_items` is distributionally identical on both
   lanes (coding: 1760 items, mean 9.778, p50 10, in **both** arms; chat: 778
   items, mean 4.371, p50 4, max 9, in **both** arms).
2. **`sibling_gather` keeps first claim** on the leftover budget — it runs
   first, and clears the completion state for any item it gathers to full
   coverage, so the chunk render (which carries the provenance headers the whole
   body does not) wins where it is already complete.
   `sibling_gather_expands_item_without_eviction_or_overbudget` is **unchanged
   and passing**.
3. **`pack_render_cap` suppresses the pass entirely.** Bounding the item is that
   lever's whole point; the cap stays `undecided`, untouched, and unentangled.

Whole change: one `Vec<Option<String>>` on `PackAccumulator` (the parent body,
kept only for partially-rendered uncapped chunk items), its `evict` line, a
17-line pass, and one test.

New test `partial_chunk_render_takes_the_whole_body_only_when_leftover_budget_allows`
pins all three arms: it asserts the defect precondition (full coverage costs
more than the whole body), that admission alone drops the span, that a roomy
pack emits the whole body without displacing the co-packed item, that a
leftover-zero pack is byte-identical to the old behaviour, and that a render cap
is not overridden.

## 3. Coding lane

180 Track R goldens (`benchmarks/data/track_r_repo_memory_golden.jsonl`,
sha256 `6f549daa…`), 495-attempt corpus (sha256 `c008142e…`), attempt-scoped
`bind_attempt_context` haystack, `--lexical-scorer bm25-code --embed-model off
--mode fast --k 10 --budget-tokens 8192`, cap off, release binaries built in
this worktree, each arm on its own auto-dropped scratch Postgres with the worker
fully drained (`done_jobs=64056`, `pending_jobs=0`, `dead_jobs=0`, 64,014 units
after 42 exact-duplicate dedups) — **identical in both arms**.

**The baseline arm was re-executed, not remembered**, and reproduces the
committed combined build to the digit: 139 packed, 30 render losses, 1760 packed
items.

| arm | r@5 | r@10 | packed |
|---|---:|---:|---:|
| before (`ccaa9e1c`) | 0.7278 | 0.7722 | 139/180 |
| **after (`f67f2b2a`)** | **0.9222** | **0.9333** | **168/180** |
| *(fused top-10 ceiling)* | — | *0.9611* | *173/180* |

| k | both | before only | after only | neither | McNemar exact p |
|---|---:|---:|---:|---:|---:|
| @10 | 139 | **0** | **29** | 12 | **3.73e-09** |
| @5 | 131 | **0** | **35** | 14 | **5.82e-11** |

**Recovered: 29 of the 30 render losses.** The one that did not,
`track_r_049`, is genuine budget pressure and is correctly *not* upgraded: its
pack already holds 24,366 chars ≈ 8.1k of the 8,192-token budget, and only one
of its ten items had room for the whole-body difference (the gold-bearing one
did not).

Miss composition, before → after:

| cause | before | after |
|---|---:|---:|
| **render loss** | **30** | **1** |
| budget | 4 | 4 |
| rerank (all fused rank ≥ 11) | 3 | 3 |
| absent from pool (`below_trust_floor` policy ceiling) | 4 | 4 |
| **total misses** | **41** | **12** |

`fused_top10_ceiling` is **173 in both arms** — the check that no retrieval or
ranking behaviour moved. A sibling owns that stage; nothing in it was touched.

168 of the 173 reachable: what is left is 4 budget + 1 render-under-budget.

## 4. Chat lane — the gate

Two `bench-lme` arms on the pinned dev split (dataset sha256 `e4667bed…`,
`--sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64
--embed-model small`, product-default `--lexical-scorer overlap`), differing
only in the render commit, each on its own scratch Postgres.

| | before | after |
|---|---:|---:|
| r@5 | 0.6145 | 0.6145 |
| r@10 | 0.6145 | 0.6145 |
| hits (of 166 scored) | 102 | 102 |
| before-only flips | — | **0** |
| after-only flips | — | **0** |
| McNemar exact p | — | **1.0** |
| per-question hit vectors identical | — | **true** |

Both arms reproduce the committed rung-7 baseline 0.6145 exactly.

**And the packed context is NOT byte-identical** — `packed_context_identical:
false`. This is the important difference from the rank-order fix, which was
mechanically unreachable here. This change *does* run on the chat lane: it
rewrites item bodies. It simply does not cost anything, because it only ever
spends leftover budget:

| | before | after |
|---|---:|---:|
| packed items, total / mean / p50 / p90 / max | 778 / 4.371 / 4 / 7 / 9 | **778 / 4.371 / 4 / 7 / 9** |
| per-item chars, mean | 5214.7 | 5465.2 (+4.8%) |
| per-item chars, p50 / p90 / p99 / max | 3504 / 12821 / 16800 / 23078 | 3626 / 13331 / 17477 / 23906 |

Items got **more complete**, and **not one item was displaced**. On a
budget-bound lane that is exactly the guarantee the reverted patch failed to
give.

## 5. Per-item render sizes, coding lane

| | before | after |
|---|---:|---:|
| packed items, total / mean / p50 / p90 / max | 1760 / 9.778 / 10 / 10 / 10 | **1760 / 9.778 / 10 / 10 / 10** |
| per-item chars, mean | 1891.6 | 1983.9 (+4.9%) |
| per-item chars, min / p50 / p90 / p99 / max | 80 / 1575 / 3938 / 4856 / 7362 | 86 / 1670 / 4011 / 4959 / 7395 |

A ~5% average growth in rendered text buys 29 questions. The pack is
slot-bound, so the cost is paid out of budget that was going unused.

## 6. Default or flag-gated

**Recommended as a default, not a flag.** Reasons, in order:

1. It repairs a defect rather than trading one behaviour for another. An item
   that shows the reader a strict subset of itself while charging nearly the
   full price is wrong on both lanes; there is no configuration in which the
   partial render is the better artifact when the whole body is affordable.
2. Its safety is structural, not empirical. The upgrade cannot evict, cannot
   reorder, cannot exceed the budget, and cannot fire at all when the leftover
   is short — so the worst case is the previous behaviour exactly.
3. The measurement agrees on both lanes: **+29/−0** where it is needed,
   **0/−0** where it is not, with packed-item counts identical in all four arms.
4. A flag would leave the defect on by default and require a second campaign to
   turn it off. The two conditions where bounding an item is genuinely wanted —
   `pack_render_cap`, and a pack with no spare budget — are already respected in
   the code.

The residual honest caveat is §7 of the coding-lane record: Track R's magnitude
is inflated by a lexically biased bank. **The defect fix is corroborated
off-bank by the chat lane's render-size distribution** (the pass fires on
LongMemEval too, and costs nothing there); the **29-question magnitude is
Track R's number, not a production one.** No promotion is taken against this
bank.

## 7. Standing notes

- Two frozen sha256 pins collide with any `memphant-core` edit — the v5 campaign
  census and the SWE-ContextBench tranche-1 rehearsal. Both are **already parked
  with loud skips** by `2938954c` / `a6d1d9b0` and were **not** re-pinned here.
  `tests/test_run_lme_v2_state_aware.py -k census` passes (5 passed, 1 skipped).
  **No new pin collides.**
- Full Python suite: **1027 passed, 15 skipped, 2 failed** — both failures
  pre-existing at base and unrelated to this work
  (`test_public_launch_gate.py::test_public_sota_claim_policy_…`,
  `test_repo_contract.py::test_spec_drift_check_passes_against_linked_syndai_docs`,
  which needs a linked Syndai checkout).
- `cargo test -p memphant-core --lib`: 137 passed, 0 failed.
  `cargo clippy --all-targets --all-features -- -D warnings`: clean.
  `cargo fmt --check`: clean.
- `pack_render_cap` remains **undecided** for the chat lane and is untouched;
  the new pass is explicitly suppressed under it.

## Reproduce

```sh
shasum -a 256 benchmarks/data/track_r_repo_memory_golden.jsonl   # 6f549daa…
shasum -a 256 docs/build-log/artifacts/track-r/corpus.jsonl      # c008142e…

cargo build --release -p memphant-server -p memphant-worker -p memphant-cli
cargo build --release -p memphant-eval --features fastembed

# One Track R arm (~75 min under load: 2100 s ingest + compile + 180 recalls).
# Repeat per commit, fresh port, run-owned scratch DB minted/dropped by the runner.
PYTHONPATH=. python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1w/<arm>-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1w/<arm>-provenance.json \
  --embed-model off --lexical-scorer bm25-code --mode fast --k 10 \
  --budget-tokens 8192 --label track-r-w1-<arm> --port <fresh> \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli

PYTHONPATH=. python3 scripts/analyze_pack_displacement.py \
  --before docs/build-log/artifacts/track-r/phase1w/before-provenance.json \
  --after  docs/build-log/artifacts/track-r/phase1w/after-provenance.json \
  --out docs/build-log/artifacts/track-r/track_r_phase1w_render_loss.json

# Chat-lane arm (~40 min each)
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant LME_DB \
  sh -c 'target/release/memphant-eval bench-lme --database-url "$LME_DB" \
    --data benchmarks/data/longmemeval_s.development.json \
    --sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64 \
    --embed-model small \
    --emit-qa docs/build-log/artifacts/rung7-packing-reader-gate/phase1w/chat-<arm>-evidence.jsonl \
    --out docs/build-log/artifacts/rung7-packing-reader-gate/phase1w/chat-<arm>-retrieval.json'

PYTHONPATH=scripts python3 scripts/analyze_lme_pack_nonregression.py \
  --before …/chat-before-retrieval.json --after …/chat-after-retrieval.json \
  --before-evidence …/chat-before-evidence.jsonl \
  --after-evidence  …/chat-after-evidence.jsonl \
  --out …/chat-lane-nonregression.json
```

## Provenance

Commits on `af-w1-render`, none pushed: **`f67f2b2a`** (the fix) →
**`d40091cc`** (render-loss target + render-size distribution in the two paired
analyzers) → **`91d486b4`** (gitignore the run outputs).

Committed artifacts:
`docs/build-log/artifacts/track-r/track_r_phase1w_render_loss.json` and
`docs/build-log/artifacts/rung7-packing-reader-gate/phase1w/chat-lane-nonregression.json`.
Per-question evidence and provenance under `…/track-r/phase1w/` and the LME-S
evidence JSONLs are gitignored under the same rule as `phase1/`, `phase1d/`,
`phase1r/`, `phase1e/` — they carry third-party event and session bodies.
