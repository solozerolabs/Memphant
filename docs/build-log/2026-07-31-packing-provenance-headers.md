# Packing: the render-loss completion keeps its provenance headers (2026-07-31)

Cost: **$0**. No reader, no judge, no paid model call. Every figure below comes
from an executed run with a named artifact. No checkbox, default, cutover,
deployment, or SOTA claim moves in this document.

Repairs a regression introduced by `f67f2b2a`
(`docs/build-log/2026-07-30-packing-render-loss-fix.md`) without giving back any
of its win.

## Headline

`f67f2b2a` let a partially chunk-rendered item take its **whole body** when the
pack's leftover budget covered the difference. That recovers the content and
throws the attribution away: the chunk render carries a per-window
`[episode …] [kind …] [date …] [turns a-b]` header on every block, and the raw
whole body carries none.

| Track R packed items (1760) | ≥1 window header | headers/item (mean) |
|---|---:|---:|
| before `f67f2b2a` | 1564 (88.9%) | 5.24 |
| after `f67f2b2a` | **163 (9.3%)** | **0.46** |
| after this fix | **1193 (67.8%)** | **4.94** |

**1401 packed items had lost segment-level attribution.** The packed context
stopped being citable at the span the reader actually used — on a product whose
positioning rests on provenance you can tap.

The completion now prefers **full chunk coverage**: the same content as the
whole body (chunk bodies are byte slices of it) with every window header intact.

| coding lane (Track R, 180) | `f67f2b2a` | this fix |
|---|---:|---:|
| packed r@10 | 0.9333 (168) | **0.9333 (168)** |
| packed r@5 | 0.9222 (166) | **0.9222 (166)** |
| fused top-10 ceiling | 173 | 173 |
| paired flips @10 / @5 | — | **0 / 0**, McNemar exact **p = 1.0** |

**The 168 holds to the digit, with per-question hit vectors identical.** The
cost is +14.4% mean rendered chars, and that growth is almost entirely the
headers themselves.

## 1. Why the whole body was the wrong completion

`sibling_gather_pass` already knew: it clears the completion state for any item
it gathers to full coverage, and its comment says the chunk render "carries the
provenance headers the whole body does not". `f67f2b2a` honoured that for
gathered items and dropped it for everything else.

Both renders carry the same text — chunk bodies are byte slices of the unit body
and together cover it. Measured on `track_r_021` slot 0: the whole body is 696
chars, and full chunk coverage carries 693 of them, the 3-char difference being
an inter-window boundary. So the choice between them is **not** a content
choice. It is only a question of whether the packed text says which window each
span came from.

Full coverage's entire extra cost over the whole body **is** the headers. That
makes the ladder obvious:

1. **Full chunk coverage** when the leftover budget covers it — same content,
   attribution intact.
2. **Bare whole body** when the headers do not fit — content still wins.
3. **Keep the partial render** when neither fits — the budget-bound no-op.

## 2. The change

`chunk_completion_pass` in `crates/memphant-core/src/lib.rs`, replacing
`whole_body_completion_pass`. The accumulator carries a `ChunkCompletion`
(the unit's chunks + its whole body) instead of a bare `Option<String>`.

Everything `f67f2b2a` guaranteed is unchanged and re-measured: admission is
untouched, the packed item set and every drop record are byte-identical, a
budget-bound pack is a no-op, and a deliberate `pack_render_cap` suppresses the
pass entirely.

**The `sibling_gather` lever was deleted on top of this**, at `2552d4c1`, by the
session that owned that decision: the completion pass subsumes it by an
inequality, its residual incremental-expansion band is provably empty, and its
only distinct behaviour was refilling past `pack_render_cap`. This document does
not re-argue that; it does **measure Track R across it**, because the deletion
was justified on LME-S and removes ~52,946 chars of packed content, and Track R
is the lane where packed content is load-bearing (§3.1).

## 3. Coding lane

180 Track R goldens (sha256 `6f549daa…`), 495-attempt corpus (sha256
`c008142e…`), attempt-scoped, `--lexical-scorer bm25-code --embed-model off
--mode fast --k 10 --budget-tokens 8192`, cap off, release binaries built in
this worktree, own auto-dropped scratch Postgres. Queue-empty asserted **from
the database on the bench credential**: `done_jobs=64056, pending_jobs=0,
dead_jobs=0`, 64,014 units after 42 exact-duplicate dedups — **identical to the
baseline arm**.

The baseline is the banked `f67f2b2a` arm's per-question provenance, paired
question-by-question.

| k | both | baseline only | fix only | neither | McNemar exact p |
|---|---:|---:|---:|---:|---:|
| @10 | 168 | **0** | **0** | 12 | **1.0** |
| @5 | 166 | **0** | **0** | 14 | **1.0** |

Miss composition is unchanged: 4 absent-from-pool, 4 budget, 3 rerank, 1
render-under-budget (`track_r_049`). `fused_top10_ceiling` = 173 in both arms.

### Render size, coding lane

| | `f67f2b2a` | this fix |
|---|---:|---:|
| packed items, total / mean / p50 / p90 / max | 1760 / 9.778 / 10 / 10 / 10 | **1760 / 9.778 / 10 / 10 / 10** |
| per-item chars, mean | 1983.9 | **2269.6** (+285.7, +14.4%) |
| per-item chars, min / p50 / p90 / p99 / max | 86 / 1670 / 4011 / 4959 / 7395 | 86 / 1959 / 4159 / 6158 / 7395 |
| header chars per item | 34.8 | **372.8** (+338.0) |

**Not one item was displaced.** The +285.7 chars/item is smaller than the
+338.0 chars/item of added header text — full coverage omits the small
inter-window gaps the whole body included, so the net content change is slightly
negative while the attribution is restored.

### The residual 32%

567 of 1760 items still carry no header, against 196 before `f67f2b2a`:

- **196** were never chunk-rendered at all (no chunks, or no chunk matched the
  query) — the whole-body path, unchanged since before either fix.
- **371** are the whole-body fallback: full coverage did not fit the leftover
  budget but the whole body did. These are the items where content genuinely
  competes with attribution, and content wins. Closing them would mean showing
  the reader less text to show it more precisely, which is the wrong trade and
  is exactly the case the superset argument covers.

## 4. Chat lane

LongMemEval-S, 178-question frozen development split (dataset sha256
`e4667bed…`), 166 scored, seed 20260710, `--k 10 --budget-tokens 8192 --pool 64
--embed-model small`, own auto-dropped scratch Postgres. Drain asserted from the
database on the bench credential: **`done_jobs=8441, pending_jobs=0,
dead_jobs=0`**, 8,441 units from 8,441 episodes across 178 tenants, **0 missing
source episodes**.

This is the lane that could move. Its pack is budget-bound where the coding
lane's is not, so the +14.4% per-item cost measured in §3 is pressure in exactly
the wrong direction here; and the `sibling_gather` deletion removes ~52,946
chars of packed content on this lane specifically.

**It did not move, and the reason is not that the mechanism was inert.**

| | `f67f2b2a` | this fix |
|---|---:|---:|
| recall (see note on k) | 0.6145 (102/166) | **0.6145 (102/166)** |
| paired b / c | — | **0 / 0** |
| per-question hit vector | — | **identical** |
| packed context byte-identical | — | **NO — the pass fired** |
| packed items, total / mean | 776 / 4.360 | **778 / 4.371** |
| per-item chars, mean | 5478.6 | **5467.4 (−11.2)** |

**Mechanism liveness, stated separately from the result.** The packed context is
**not** byte-identical between arms (`packed_context_identical: false`), so this
is a live no-op, not an unfired one. That distinction is the whole value of the
arm: an inert pass and a neutral pass produce the same recall number and mean
opposite things.

**The feared regression did not occur — it inverted.** Two *more* items were
packed and mean per-item chars went *down* by 11.2. Full chunk coverage omits the
inter-window gaps the whole body carries, and on this lane that saving exceeds
the header cost, so the change is marginally cheaper per item rather than 14.4%
dearer. No item was displaced by budget pressure.

**What this result may not be called.** `b = c = 0`, so **n_d = 0**. Two-sided
exact McNemar has no rejection region below **n_d = 6**, which means this test
had *zero power at any effect size*. `p = 1.0` here is arithmetic, not evidence.
This is a valid **non-regression** statement — recall did not fall — and it is
**invalid as evidence of equivalence**. It must never be cited as "no
difference".

**Note on k.** `recall_at_5` and `recall_at_10` are both 0.6145 and that is not a
coincidence: the maximum `first_answer_rank` ever observed on this slice is
**5**, so `hit@10` is identical to `hit@5` on all 166 scored rows. One figure is
reported here deliberately. Any chat-lane number previously reported "at k=10" is
the k=5 result relabelled.

**Not captured.** The per-drop-reason breakdown (the Budget-drop count
specifically) is **not present in these artifacts** — `chat-*-retrieval.json`
carries no `drop_reason` field — so the claim that budget pressure did not bite
rests on the item-count and char-distribution deltas above, not on a drop census.
Recording drop reasons on this lane would close that gap.

**Licence note, unrelated to this result:** `benchmarks/manifests/longmemeval_s.lock.json`
carries **no licence field at all**, unlike the v2 and memsyco locks which pin
the LICENSE blob's sha256. This is the instrument we lean on hardest.

## 5. Convergence with `4309a62b`

A parallel session fixed the same red test **test-only** at `4309a62b`, arguing
the far-window assertion was a budget artifact rather than a redaction contract.
That is correct and this work reached it independently. The two verdicts differ
only on whether core should change: theirs is a **content** argument (whole body
⊇ chunk selection, nothing lost), which is true and does not address
attribution, which is not a superset. Merged at `01888bba`, keeping their
tight-budget arm and its original assertions verbatim, and replacing their roomy
arm's `item.body == SESSION_BODY` with the full-coverage assertions plus a new
one: no window may appear without its header.

## 6. Verification

- `cargo test --workspace` on the merged head: **677 passed, 0 failed, 96
  ignored**. This is the floor now, not `-p memphant-core --lib` (137 tests),
  which excluded all 30 files in `memphant-core/tests/` and is how the
  regression shipped.
- `cargo clippy --all-targets --all-features -- -D warnings`: clean.
  `cargo fmt --check`: clean.
- Postgres-gated (`-- --ignored`, own scratch DB): 78 passed. Three failures
  seen under load are **not** defects, each established by re-execution:
  `retain_resource_registers_and_enqueues` and
  `the_worker_pool_counts_queued_jobs_across_every_tenant` **pass in isolation**
  and fail only when one scratch DB is shared across a crate, because
  `pending_worker_job_count` is global across tenants;
  `fast_mode_recall_holds_release_hot_path_slo_on_postgres` is a latency SLO
  that failed at loadavg 50–72 and **passes on a quiet machine**.
- `pytest`: 1052 passed, 1 failed. The failure,
  `test_public_launch_gate.py::test_public_sota_claim_policy_…`, reproduces at
  `e300d298` by checkout; its cause is `playwright: command not found`, and the
  `npm test` call was introduced by **`bb517fed`** (2026-07-03). Environmental.
- `test_wsa_migration_contract` ×2 and the `test_gate_runtime` drain test,
  reported red elsewhere, are **green here** (84 passed, 2 skipped).
- Two frozen sha256 pins that collide with any `memphant-core` edit — the v5
  campaign census and the SWE-ContextBench tranche-1 rehearsal — were **not**
  re-pinned.

## Reproduce

```sh
shasum -a 256 benchmarks/data/track_r_repo_memory_golden.jsonl   # 6f549daa…
shasum -a 256 docs/build-log/artifacts/track-r/corpus.jsonl      # c008142e…
shasum -a 256 benchmarks/data/longmemeval_s.development.json     # e4667bed…

cargo build --release -p memphant-server -p memphant-worker -p memphant-cli
cargo build --release -p memphant-eval --features fastembed

PYTHONPATH=. python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1w9/prov-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1w9/prov-provenance.json \
  --embed-model off --lexical-scorer bm25-code --mode fast --k 10 \
  --budget-tokens 8192 --label track-r-w9-prov --port 8391 \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli

PYTHONPATH=. python3 scripts/analyze_pack_displacement.py \
  --before docs/build-log/artifacts/track-r/phase1w9/f67f2b2a-baseline-provenance.json \
  --after  docs/build-log/artifacts/track-r/phase1w9/prov-provenance.json \
  --out docs/build-log/artifacts/track-r/track_r_w9_provenance_headers.json

bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant LME_DB \
  sh -c 'target/release/memphant-eval bench-lme --database-url "$LME_DB" \
    --data benchmarks/data/longmemeval_s.development.json \
    --sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64 \
    --embed-model small \
    --emit-qa docs/build-log/artifacts/rung7-packing-reader-gate/phase1w9/chat-prov-evidence.jsonl \
    --out docs/build-log/artifacts/rung7-packing-reader-gate/phase1w9/chat-prov-retrieval.json'

PYTHONPATH=scripts python3 scripts/analyze_lme_pack_nonregression.py \
  --before .../chat-after-retrieval.json --after .../chat-prov-retrieval.json \
  --before-evidence .../chat-after-evidence.jsonl \
  --after-evidence  .../chat-prov-evidence.jsonl \
  --out .../chat-lane-provenance-nonregression.json
```
