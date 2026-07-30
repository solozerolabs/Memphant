# Packing: the k output slots now go to the ranking stage's own top-k (2026-07-30)

Cost: **$0**. No reader, no judge, no paid call. Every figure below comes from
an executed run with a named artifact. No checkbox, default, cutover,
deployment, or SOTA claim moves in this document.

Answers `docs/build-log/2026-07-30-phase1-golden-banks-and-retrieval-probe.md`
§5(b): *packing displaces gold it already ranked* — 56 of the 147 top-10 golds,
a preregistered target.

## Headline

- The displacement had **two** causes, not one, and the measurement could not
  tell them apart. Splitting them was the first result.
- **Cause 1 — rank displacement (27 of 56).** With the pack full at k, a
  candidate from fused ranks 11–64 could evict an already-admitted top-10 item,
  judged by a formula that is not the order the pack was handed. Fixed.
- **Cause 2 — render loss (28 of 56).** The gold unit *did* take a packed slot;
  its rendered body just did not contain the gold span. Not a displacement at
  all. Quantified, **not** fixed here, and §5 says why bundling it would have
  been wrong.
- Track R r@10 **0.5056 → 0.6278** (91 → 113 of 180), paired **22 gains, 0
  losses**, McNemar exact **p = 4.77e-07**. r@5 0.4500 → 0.4611 (3 gains, 1
  loss, p = 0.625, not significant).
- Chat lane: **zero** flips, and the packed context is byte-identical on all 178
  LME-S questions after normalizing per-run episode ids.

## 1. The mechanism, named and traced

`admit_or_drop` (`crates/memphant-core/src/lib.rs`) has an "output already
full" branch that fires once `acc.items.len() >= k`. Until R1.5-T0 it was
unreachable in Fast mode, because `scan_limit == k == output_limit`. R1.5-T0
widened the packing scan window to the pool floor — `recall_pool_depth`, 64 by
default — so in Fast the branch now sees candidates at fused ranks 11..64.

What it did with them: run `replacement_index` over the already-packed items and
**evict the weakest one** whenever the newcomer scored higher under
`packing_relevance_score`:

```rust
candidate.fused_score
    + exact_score(...) + lexical_score(...) + token_set_overlap_score(...)
    + candidate.decay.retrievability
```

That is not the ordering the ranking stage produced. It is `fused_score` plus
four packing-local terms, so a rank-40 candidate with heavy lexical overlap
outbids a rank-3 item and takes its slot. Both the evictee and the losing
newcomer are recorded as `RecallDropReason::Rerank`, which is why the Phase 1b
summary saw `rerank` dominate.

The gate `rank_based_ordering_active` existed to switch this contest **off**
when Deep, a cross-encoder, or submodular ordering governed the list. So the
contest ran in exactly one configuration: **the plain Fast default, with no
reranker** — which is the product default and the Track R run.

This reconciles the 2026-07-12 verdict recorded in the code
(`[[memphant-packing-gate-verdict]]`, 276/276 suppressions correct, gate
"measured-permanent"). That measurement was taken on the **cross-rerank arm**,
where `rank_based_ordering_active` is true and the contest never executes. It
measured the gate's *off* state. It was never evidence about the *on* state.

### Trace evidence (baseline arm, per-question)

| signal | value |
|---|---|
| gold in candidate pool | 176/180 |
| gold at fused rank ≤ 10 | 147/180 |
| gold in the packed context | **91/180** |
| in-pool-unpacked | 85 |
| of those, gold `Rerank`-dropped | **56** |
| of those, gold at fused rank ≤ 10 | **27** — best-ranked such gold sat at **fused rank 3** |
| candidates scanned per question | 64 on 176/180 (`packed_size` + drops) |
| `budget_share_of_in_pool_unpacked` | 0.0118 — not a budget problem |

So on 27 questions an item the ranker put in the top 10 — one of them third —
was thrown out of a full pack for something from below the cut.

## 2. The 28 the instrument could not classify

28 of the 56 carried **no drop reason at all**, which the Phase 1b runner could
only report as `not_in_dropped_items`. That is ambiguous between "never took a
slot" and "took a slot and was rendered without its span", and the two demand
opposite fixes.

The runner now carries the packed items' `unit_id`s beside their bodies and
records `gold_best_unit_packed` / `gold_units_packed` (commit `cc69a608`). The
answer is unambiguous:

| of the 56 | `gold_drop_reason` | gold units in packed slots |
|---:|---|---|
| 27 | `rerank` | 0 |
| 28 | *(none)* | **1 or 2** |
| 1 | `budget` | 0 |

All 28 had a gold unit **in the packed context**. Cause: `packed_render` gives
each item a render budget of `whole_body.min(request_budget)` and then renders
from contextual chunks, but each chunk block is charged its provenance header on
top of its body — so full coverage always costs *more* than the whole body, and
an uncapped chunked item can never emit all of itself. It drops chunks while
charging nearly the whole-body price, and the gold span goes with them.

**That is not displacement.** Half the preregistered 56 was a different defect
wearing the same summary statistic.

## 3. The fix

`03fa1266`, one branch in `admit_or_drop`: once the output is full, the
established order wins and the late candidate is dropped — never swapped in.
`rank_based_ordering_active` existed only to switch that contest off and is
deleted with it — the field, its computation, and the `replacement_index` call
in this branch all go. External rank signals still govern, by sorting the list
before the fill, which is where they always applied. Whole commit: +80/−53 in
`lib.rs`, most of it the two tests and the comment recording the measurement.

**The budget-driven replacement below it is untouched.** That one is a
legitimate substitution: the candidate would not fit as a fresh addition
regardless of rank, so trading it against a packed item is a real choice rather
than a re-score.

Tests: `full_pack_keeps_rank_order_against_a_lexically_stronger_late_candidate`
(new; a below-the-cut candidate that wins every packing-local term must not take
a slot) and `quantity_rollup_consumes_one_slot_and_leaves_the_rest_in_rank_order`
(rewritten — it asserted the removed contest; its real guard, that an
authoritative projection takes one slot without freezing the pack, is kept).

## 4. Measurement

Both arms: same 180 goldens (`benchmarks/data/track_r_repo_memory_golden.jsonl`,
sha256 `6f549daa…`), same 495-attempt corpus (sha256 `c008142e…`), same
attempt-scoped `bind_attempt_context` haystack, `--embed-model off`,
`--mode fast --k 10 --budget-tokens 8192`, cap off, release binaries built in
this worktree, each on its own auto-dropped scratch Postgres with the worker
fully drained (`compiled=64056`, `pending_jobs=0`, `dead_jobs=0`, 64,014 units
after 42 exact-duplicate dedups).

**The baseline arm was re-executed, not remembered.** It reproduces the
committed Phase 1b run to the digit: 91 hits, 85 in-pool-unpacked, `{budget 1,
not_in_dropped_items 28, rerank 56}`, `packed_items_total` 1760,
`packed_item_chars_total` 2,018,765.

| arm | r@5 | r@10 | packed |
|---|---:|---:|---:|
| baseline | 0.4500 | 0.5056 | 91/180 |
| **rank-order fix** | **0.4611** | **0.6278** | **113/180** |
| *(MemPhant fused top-10 ceiling)* | — | *0.8167* | *147/180* |

| k | both | before only | after only | neither | McNemar exact p |
|---|---:|---:|---:|---:|---:|
| @10 | 91 | **0** | **22** | 67 | **4.77e-07** |
| @5 | 80 | 1 | 3 | 96 | 0.625 |

**Recovered: 22 of the 56 preregistered displaced golds.** Not 27, because 5 of
the 27 eviction cases now get their gold unit into a slot and lose it to cause 2
instead — visible as render losses rising 28 → 33.

The displacement mechanism is **eliminated, not reduced**:

| | baseline | after |
|---|---:|---:|
| golds at fused rank ≤ 10 still unpacked | 56 | 34 |
| … of those, `Rerank`-evicted | **27** | **0** |
| … of those, render losses | 28 | 33 |
| … of those, budget | 1 | 1 |
| best (lowest) fused rank of any gold `Rerank` drop | **3** | **12** |

After the fix, every gold at fused rank ≤ 10 that reaches the pack takes a
packed slot; the only golds that lose the drop contest sit at rank 12 or worse,
i.e. genuinely below the cut. **All 113 hits are exactly the questions whose
gold is at fused rank ≤ 10 and whose packed body retains the span.**

`fused_top10_ceiling` is 147/180 in both arms — the check that no retrieval or
ranking behaviour moved. The retrieval stage was not touched; a sibling agent
owns it.

Artifact: `docs/build-log/artifacts/track-r/track_r_phase1d_packing_rank_order.json`
(committed). Per-question artifacts under `…/track-r/phase1d/` carry third-party
event bodies and are gitignored, like `phase1/`.

## 5. Chat-lane non-regression

Packing is shared, so a code-lane win that costs the chat lane is not a win.
Two `bench-lme` arms on the pinned dev split (dataset sha256 `e4667bed…`,
`--sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64
--embed-model small`), differing only in the packing commit, each on its own
scratch Postgres.

- r@5 and r@10 **0.6145 in both arms** — reproducing the committed rung-7
  baseline exactly.
- **0 flips** in either direction across all 166 scored questions (McNemar
  p = 1.0).
- Stronger than score parity: after normalizing per-run episode UUIDs the
  **packed context is byte-identical on all 178 questions**.

The mechanism predicts this. LME-S packs **2–9 items** per question and never
reaches k=10, so the output-full branch is unreachable there — the same
budget-bound pathology the rung-7 diagnosis recorded as 64/64 `Budget` drops,
seen from the other side. The two lanes fail at opposite ends of the pack.

Artifact: `docs/build-log/artifacts/rung7-packing-reader-gate/phase1d/chat-lane-nonregression.json`.

`pack_render_cap` is untouched and remains **undecided** for the chat lane.
Nothing here entangles the two.

## 6. Why the render loss was not bundled in

The one-line fix is available — when no cap is set and the chunk selection is
partial, emit the whole body, which costs at most the budget already reserved.
It was implemented, measured against the unit suite, and **reverted**, for three
reasons:

1. **It pushes per-item cost up.** Whole-body cost ≥ chunk-selection cost
   always. The chat lane is budget-bound (64/64 `Budget` drops, `packed_size`
   median 4), so the same change that recovers code-lane golds is pointed
   straight at the chat lane's known pathology. Opposite-signed effects on two
   lanes must not ride in one commit.
2. **It collides with `sibling_gather`**, whose entire purpose is to re-expand a
   partially-rendered chunked item after the fill. The fallback pre-empts it, and
   `sibling_gather_expands_item_without_eviction_or_overbudget` fails — correctly.
   Reshaping a lever's own invariant test to accommodate an unlanded change is
   the wrong order of operations.
3. **It is adjacent to `pack_render_cap`**, which the standing note says is
   `undecided`, not rejected, and must not be entangled with this work.

So it is recorded as the next localized, already-quantified target: **33 of the
34 remaining rank-≤10 misses**, with its own paired chat-lane arm required
before it lands. Closing it would take the pack from 113 to ~146 against a
147/180 fused ceiling.

## 7. Standing notes

- The shared server harness still **closes inherited packing env vars**; the
  test proving an ambient `MEMPHANT_PACK_RENDER_CAP=9999` never reaches a
  cap-OFF arm is unchanged and passing. Blanket inheritance was not reopened.
- **Owner decision required:**
  `tests/test_run_lme_v2_state_aware.py::test_canonical_census_source_inventory_covers_declared_campaign_code`
  fails on this branch and passes at its base `a96c289c`. Any edit to
  `memphant-core` moves `source_set_sha256`, which the v5 campaign census pins.
  That pin exists so a paid campaign's binary provenance cannot drift silently,
  so it was **not** bumped here. v5 is parked per the plan of record; if this
  fix lands on a branch the campaign builds from, the census needs re-deriving
  under its own authorization.
- Pre-existing at base, untouched by this work:
  `test_public_launch_gate.py::test_public_sota_claim_policy_…` and
  `test_repo_contract.py::test_spec_drift_check_passes_against_linked_syndai_docs`
  (needs a linked Syndai checkout).

## Reproduce

```sh
# Inputs (both hashes verified before the runs)
shasum -a 256 benchmarks/data/track_r_repo_memory_golden.jsonl   # 6f549daa…
shasum -a 256 docs/build-log/artifacts/track-r/corpus.jsonl      # c008142e…

cargo build --release -p memphant-server -p memphant-worker -p memphant-cli

# One Track R arm (~40 min: ingest + compile + 180 recalls). Repeat per commit,
# fresh port, run-owned scratch DB minted and dropped by the runner.
PYTHONPATH=. python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1d/<arm>-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1d/<arm>-provenance.json \
  --embed-model off --mode fast --k 10 --budget-tokens 8192 \
  --label track-r-phase1d-<arm> --port <fresh> \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli

PYTHONPATH=. python3 scripts/analyze_pack_displacement.py \
  --before docs/build-log/artifacts/track-r/phase1d/baseline-provenance.json \
  --after  docs/build-log/artifacts/track-r/phase1d/rankorder-provenance.json \
  --out docs/build-log/artifacts/track-r/track_r_phase1d_packing_rank_order.json

# Chat-lane arm (~30 min each), fastembed feature required for --embed-model small
cargo build --release -p memphant-eval --features fastembed
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant LME_DB \
  sh -c 'target/release/memphant-eval bench-lme --database-url "$LME_DB" \
    --data benchmarks/data/longmemeval_s.development.json \
    --sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64 \
    --embed-model small \
    --emit-qa docs/build-log/artifacts/rung7-packing-reader-gate/phase1d/chat-<arm>-evidence.jsonl \
    --out docs/build-log/artifacts/rung7-packing-reader-gate/phase1d/chat-<arm>-retrieval.json'

PYTHONPATH=scripts python3 scripts/analyze_lme_pack_nonregression.py \
  --before …/chat-baseline-retrieval.json --after …/chat-rankorder-retrieval.json \
  --before-evidence …/chat-baseline-evidence.jsonl \
  --after-evidence  …/chat-rankorder-evidence.jsonl \
  --out …/chat-lane-nonregression.json
```

Runnable checks: `python3 -m pytest tests/test_code_lane_run_memphant.py -q`
(31 passed), `cargo test -p memphant-core --lib` (128 passed),
`cargo clippy --all-targets --all-features -- -D warnings` (clean),
`cargo fmt --check` (clean).

## Provenance

Commits on `af-packing`, none pushed: `03fa1266` (fix) → `cc69a608`
(instrument + paired analyzers) → `26a3c032` (Track R evidence) → `4628d88b`
(chat-lane evidence).
