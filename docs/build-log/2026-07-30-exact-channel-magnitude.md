# Ranking: the Exact channel now carries its own magnitude (2026-07-30)

Cost: **$0**. No reader, no judge, no paid model call, no new corpus. Every
figure below comes from an executed run with a named artifact. No checkbox,
default, cutover, deployment, or SOTA claim moves in this document.

Adjudicates a regression **our own work introduced**: `03fa1266` (the packing
rank-order fix, merged as `e99b912d`) broke
`cargo test -p memphant-eval --test syndai_trace_compare`.

## 0. The regression is real and it is ours

`syndai_coding_continuity_fixture_families_pass` **passes at `a96c289c`** and
fails from `e99b912d` onward. Verified by checking out `a96c289c` into its own
worktree and running the full suite there, not by inference:

| revision | `cargo test --workspace --no-fail-fast` |
|---|---|
| `a96c289c` (before the packing fix) | 664 passed, **0 failed**, 92 ignored — fixture `ok` |
| `af-packadj` HEAD before this work | 674 passed, **1 failed**, 94 ignored — fixture FAILED |

Re-confirmed after rebaselining onto trunk: with the render-loss completion pass
merged in (`cca8b329`), the fixture is still red without this change
(`missing=["mem_rollout_task_state", "mem_error_budget_constraint"]`) and green
with it. The render-loss pass is post-fill and budget-conditional; on this
fixture the two answers never take a slot at all, so it cannot reach them.

An earlier agent recorded it as pre-existing. That was true *relative to its own
base*, which already contained `03fa1266`. §5 treats that as a process finding,
not an individual one.

Failure:
`task-plus-semantic-composite-trace-compare.yaml: missing=["mem_rollout_task_state", "mem_error_budget_constraint"]`
— i.e. at k=2 the pack contained **both keyword-stuffed distractors and neither
answer**.

## 1. Why fused rank puts the chatter above the answers

Instrumented inside `pack_recall_context` (env-gated dump, since removed) so the
numbers are the packer's own, not a re-derivation. Query: *"Which task is the
checkout retry rollout paused on and which constraint gates resuming it?"*

| fused ord | unit | fused | exact | lexical | overlap | retriev | `packing_relevance_score` |
|---:|---|---:|---:|---:|---:|---:|---:|
| 0 | `mem_rollout_chatter_0` | **0.0550276** | 0.33333 | 0.66667 | 0.35294 | 0.84588 | 2.25385 |
| 1 | `mem_rollout_chatter_1` | 0.0541468 | 0.33333 | 0.66667 | 0.35294 | 0.84588 | 2.25297 |
| 2 | `mem_rollout_task_state` | 0.0539235 | **1.00000** | 0.66667 | 0.35294 | 0.84588 | **2.91942** |
| 3 | `mem_error_budget_constraint` | 0.0535178 | **1.00000** | 0.30769 | 0.16667 | 0.84588 | **2.37376** |

The two answers are **last**, by 0.4% and 0.8%. The arithmetic is entirely
mechanical. Fusion is weighted RRF, `weight / (60 + channel_rank)`, times
`decay.retrievability`:

- **The Exact channel ranks the two answers 1 and 2** and both distractors 3 and
  4 — it is the one channel that gets this right. Its weight is `1.0`.
- **The lexical family votes with weight 3.0** — `Lexical` (1.0) plus
  `Semantic` (2.0), which despite its name is
  `token_set_overlap_score`, a pure body-token scorer (the source comment says
  so: *"This is a LEXICAL scorer … no embeddings are involved"*). With a BM25
  scorer selected, `Bm25` stands in for both and carries the same combined 3.0.
- The distractor bodies carry nearly every query term, so they lead the lexical
  family. **3 votes beat 1**, by 0.0013 pre-decay.

So the Exact channel's decisive 1.000-vs-0.333 margin — a **3×** difference in
subject-key coverage — is flattened by rank-only RRF into a single rank
position, worth about **0.0005**. That is the whole defect.

**This is a ranking problem, not a packing problem.** The proof is arithmetic:
subtract `exact_score` from `packing_relevance_score` and the remainder
reproduces the fused order exactly —

| unit | `prs − exact` |
|---|---:|
| `mem_rollout_chatter_0` | 1.92052 |
| `mem_rollout_chatter_1` | 1.91964 |
| `mem_rollout_task_state` | 1.91942 |
| `mem_error_budget_constraint` | 1.37376 |

— same order, same relative gaps. **`exact_score` is the entire content of the
eviction contest `03fa1266` deleted.** The contest was not a defence against
lexical chatter in general; it was one channel's magnitude being re-applied,
late, at packing time, by a formula nobody opted into. `03fa1266` was right that
the mechanism was wrong. It was wrong to conclude nothing was being defended.

## 2. Better lexical scoring does **not** dissolve it

Both BM25 arms were run on the same fixture. Full-precision channel scores:

| scorer | `task_state` (real answer) | `chatter_0` | `chatter_1` |
|---|---:|---:|---:|
| `bm25-control` | 2.64491128921508789 | **2.64491128921508789** | **2.64491128921508789** |
| `bm25-code` | 2.69619059562683105 | 2.69619083404541016 | 2.69619083404541016 |
| `Overlap` (Lexical pass) | 0.666666686534881592 | 0.666666686534881592 | 0.666666686534881592 |
| `Overlap` (Semantic pass) | 0.352941185235977173 | 0.352941185235977173 | 0.352941185235977173 |

- Under **`bm25-control` the real answer and both distractors tie bit-for-bit**.
  The order comes entirely off the fused sort's alphabetical body tie-break
  (`left.unit.body.cmp(&right.unit.body)`) — `"Checkout…0."` < `"Checkout…1."` <
  `"The checkout…"`.
- Under **`bm25-code` the real answer scores marginally *lower*** (2.4e-7
  relative). It loses on score, not only on tie-break.
- Under the default `Overlap` scorer both lexical passes are exact three-way
  ties.

Consequently the **fused scores are bit-identical across all three scorers**
(0.0550276 / 0.0541468 / 0.0539235 / 0.0535178) and all three pack the same two
distractors. IDF plus length normalisation does not defeat keyword stuffing
here: it draws, or loses by a rounding error. The BM25 work is a real
improvement to lexical scoring, and it is orthogonal to this failure.

## 3. The fix

One expression in the fusion loop
(`crates/memphant-core/src/lib.rs`, commit `3fc4eede`):

```rust
let magnitude = if pass == ChannelPass::Exact { score } else { 1.0 };
let contribution = magnitude
    * channel_weight(pass, &request.query, temporal_window.as_ref())
    / (60.0 + channel_rank as f32);
```

Rank-only RRF is correct for channels whose scores share no scale — a BM25
score and a cosine similarity are only comparable by rank. `exact_score` is not
one of those: it is the **fraction of a unit's curated `fact_key` tokens the
query covers**, a calibrated 0..1. Discarding that number is a loss of real
information, and it is the number that separates a curated subject key from a
stuffed body. A body can be stuffed; a `fact_key` is written at retain time and
stuffing it is a different, visible act.

Properties:

- **Within-channel order is unchanged.** Score is non-increasing in rank and
  `1 / (60 + rank)` is decreasing, so `score / (60 + rank)` is strictly
  decreasing. Only cross-channel magnitude moves.
- **`03fa1266`'s contract is untouched.** The pack still fills its `k` slots in
  the ranking stage's order; no second ordering is reintroduced at packing time.
  This is the resolution the diagnosis in §1 points at: fix the ranking the
  contest was papering over, rather than restore the paper.
- **Inert where `fact_key` carries no query signal.** A unit with
  `exact_score == 0` never enters the Exact channel at all
  (`(score > 0.0).then(...)`), so nothing changes for it.

Resulting fused order on the fixture — both answers first, by a **16%** margin
rather than 0.4%:

| ord | unit | fused |
|---:|---|---:|
| 0 | `mem_rollout_task_state` | 0.0539235 |
| 1 | `mem_error_budget_constraint` | 0.0535178 |
| 2 | `mem_rollout_chatter_0` | 0.0460765 |
| 3 | `mem_rollout_chatter_1` | 0.0453355 |

### Options considered and rejected

- **Restore a bounded contest gated on `exact_score`.** Would also pass the
  fixture, and `exact_score` is the non-lexical signal the brief names. Rejected
  as ~30 lines against 3, and because it re-introduces a second ordering at pack
  time — exactly the thing `03fa1266` correctly removed — to compensate for a
  ranking stage that is still wrong for every other consumer.
- **Break channel-score ties by `exact_score` instead of alphabetically.**
  Well-motivated on its face — the body tie-break is arbitrary noise, and §2
  shows the ties are real and decisive. **Executed** against `3fc4eede^`:
  `missing=["mem_error_budget_constraint"]`. It recovers one of the two answers
  and no more, because the second answer is not tied — it is genuinely weaker on
  both lexical passes (0.30769 / 0.16667).
- **Raise `EXACT_CHANNEL_WEIGHT` from 1.0 to 3.0**, matching the lexical family.
  **Executed** against `3fc4eede^`: `missing=["mem_rollout_task_state"]` — still
  one short, and a *different* one. RRF flattening means adjacent ranks differ
  by ~1.6% whatever the weight, so no weight recovers a 3× score margin. The
  magnitude, not the weight, is what was lost.

Neither rejected option is a partial version of the fix: they each recover a
different single answer, and only restoring the magnitude recovers both.

## 4. Measurements

### 4.1 The fixture

`cargo test -p memphant-eval --test syndai_trace_compare` — **2 passed, 0
failed** on the merged trunk. Both Syndai lanes green, `answer_bearing_recall`
1.0.

New ranking-stage guard,
`memphant-core/tests/recall_trace_golden.rs::keyword_stuffed_body_does_not_outrank_a_fully_covered_subject_key`,
verified red/green by checkout: **FAILED** at `3fc4eede^`, `ok` with the change.

### 4.2 Track R — coding lane, against the current 168/180 trunk baseline

Measurement base is **`f67f2b2a`**, the render-loss commit that produced the
committed 168/180. Both arms differ only in the one expression from §3.
`f67f2b2a` predates W3.3, deliberately: see §5.4 — the coding lane cannot be run
on a W3.3-containing build, so this is the same lineage the 168 figure itself
was taken on, which is what makes the pairing valid.

Same 180 goldens (`6f549daa…`), same 495-attempt corpus (`c008142e…`),
attempt-scoped haystack, `--lexical-scorer bm25-code --embed-model off
--mode fast --k 10 --budget-tokens 8192`, cap off, release binaries, each arm on
its own auto-dropped scratch Postgres. **Worker fully drained in both arms:**
`episodes=64056`, `done_jobs=64056`, `pending_jobs=0`, `dead_jobs=0`, 64,014
units after 42 exact-duplicate dedups.

**The baseline arm was re-executed, not remembered**, and reproduces the
committed render-loss run to the digit: 168 packed, r@10 0.9333, 8
in-pool-unpacked, `{budget 4, not_in_dropped_items 1, rerank 3}`, 1760 packed
items.

| arm | r@5 | r@10 | packed |
|---|---:|---:|---:|
| base (`f67f2b2a`) | 0.9222 | 0.9333 | 168/180 |
| **fix (`f67f2b2a` + `3fc4eede`)** | 0.9222 | 0.9333 | **168/180** |
| *(fused top-10 ceiling)* | — | *0.9611* | *173/180* |

| k | both | before only | after only | neither | McNemar exact p |
|---|---:|---:|---:|---:|---:|
| @10 | 168 | **0** | **0** | 12 | **1.0** |
| @5 | 166 | **0** | **0** | 14 | **1.0** |

**Zero flips in either direction.** Stronger than score parity: every recorded
statistic is identical between the arms, including `packed_items_total` 1760 and
`packed_item_chars_total` **3,491,737**. Fused top-10 ceiling 173 in both.

**And the mechanism predicts exactly this.** Track R units are compiled from raw
episodes, which reach `derive_fact_key` with no subject and no predicate and so
get `"{scope_uuid}:auto:{16 hex}"`. No natural-language query token is
`tokens_related` to a UUID fragment, so `exact_score == 0` for every candidate,
so **no Track R candidate ever enters the Exact channel** — and
`(score > 0.0).then(...)` means the changed expression is never evaluated there.
The change is not "small enough not to hurt" the coding lane; it is *unreachable*
on it. The 0/0 result is the confirmation, not a lucky null.

This also says something uncomfortable about the instrument, and it belongs in
the record: **Track R cannot detect a regression in this channel either.** It is
blind to the entire Exact/subject-key path. It is the right non-regression check
here and a useless sensitivity check.

Artifacts: `docs/build-log/artifacts/track-r/track_r_packadj_exact_magnitude.json`
(committed); per-arm provenance under `…/track-r/packadj2/` (gitignored —
third-party event bodies).

### 4.3 Chat lane — LME-S non-regression

Ranking is shared, so a coding-lane fixture fixed at the chat lane's expense is
not a fix. Two `bench-lme` arms on the pinned dev split (dataset sha256
`e4667bed…`, `--sample 178 --seed 20260710 --k 10 --budget-tokens 8192
--pool 64 --embed-model small`), same two commits as §4.2, each on its own
scratch Postgres.

- r@5 and r@10 **0.6144578313253012 in both arms** — reproducing the committed
  rung-7 baseline exactly, on the render-loss lineage.
- **0 flips** in either direction across all 166 scored questions, McNemar
  exact **p = 1.0** at both k. Abstention 7/12 in both.
- Stronger than score parity: `packed_context_identical: true` and
  `per_question_vector_identical: true` — the **packed context is byte-identical
  on all 178 questions**, and the render-size distribution is unchanged (778
  items, mean 4.371, p50 4, max 9; item chars mean 5465.2, max 23906 in both).

Note this is a *stronger* null than the render-loss pass produced on the same
lane, where `packed_context_identical` was `false` because that change does run
on chat bodies. This one does not run at all here: LongMemEval sessions are
retained as episodes, so their units also carry auto-derived `fact_key`s and the
Exact channel is likewise empty.

Artifact:
`docs/build-log/artifacts/rung7-packing-reader-gate/packadj2/chat-lane-nonregression.json`.

### 4.4 Suites

Merged trunk (`a83981ac`) with the change:

| suite | result |
|---|---|
| `cargo fmt --all --check` | clean |
| `cargo clippy --workspace --all-targets` | clean |
| `cargo test --workspace --no-fail-fast` | 676 passed, **1 failed**, 95 ignored |
| `python3 -m pytest tests/ -q` | 1045 passed, **4 failed**, 15 skipped |

Every failure, and whether it reproduces at `a96c289c` — **verified by checking
out `a96c289c` into its own worktree and running both suites there** (664
passed / 0 failed Rust; 1025 passed / 1 failed Python):

| failure | at `a96c289c` | attribution |
|---|---|---|
| `recall_chunk_renders_matched_window_plus_neighbour` (Rust) | **passes** | not mine — bisected to `f67f2b2a` (render-loss); red on `accuracy-first` tip |
| `test_public_launch_gate::test_public_sota_claim_policy_…` | **fails identically** | environmental: `sh: playwright: command not found` |
| `test_wsa_migration_contract::test_apply_runner_dry_run_reports_ordered_migrations` | n/a (migration set differs) | not mine — reproduces on `accuracy-first` tip `1bddcda6`; migration `005` landed without updating the contract's migration count |
| `test_wsa_migration_contract::test_apply_runner_executes_migration_and_ledger_in_one_transaction` | n/a | same as above |
| `test_gate_runtime::test_drain_worker_uses_one_binary_drain_without_structured_provider` | n/a | not mine — reproduces on `accuracy-first` tip `1bddcda6`; from the drain fix |

All four Python failures are a strict subset of the six on `accuracy-first` tip
(`1bddcda6`), measured in the same session. **No failure on this branch is
attributable to this change.**

The parked **v5 census** test now skips on its own (`pytest.skip: v5 census
identity is frozen and v5 is parked`) — it was **not** re-pinned. The terminal
SWE-ContextBench rehearsal was not touched.

## 5. Coverage-gap audit

Both prior agents' non-regression checks missed this, for two different reasons,
and the second reason is the more dangerous one.

### 5.1 What each agent actually ran

**The packing fix (`03fa1266`)** recorded its runnable checks as:
`pytest tests/test_code_lane_run_memphant.py`, **`cargo test -p memphant-core --lib`**,
`clippy`, `fmt`, plus two Track R arms and two LME-S arms.

`cargo test -p memphant-core --lib` runs **only the unit tests compiled into one
crate's lib target**. It excludes every integration test in
`crates/*/tests/` — including all 30 files under `crates/memphant-core/tests/`
— and every other crate in the workspace. The fixture that caught this lives in
`crates/memphant-eval/tests/syndai_trace_compare.rs`. It was never executed.

**The W3.3 RLS work** did run `cargo test --workspace --no-fail-fast`, saw the
failure, and recorded it as *"reproduces on the stashed branch base —
pre-existing, not from this work."* That claim was true and useless: its base
already contained `03fa1266`. **"Reproduces at my base" is not "pre-existing."**
A base-relative attribution cannot distinguish a defect that predates the
program from one a sibling branch introduced last week, and on a repo with a
dozen concurrent worktrees that distinction is the whole point. The cheap
correct move is one `git bisect run` against trunk, or at minimum re-testing at
the last commit that touched the failing subsystem.

### 5.2 Committed suites that exercise packing or ranking and were run by neither

All of these are ordinary `cargo test` targets requiring no database, no
network, and no model call. None is in `-p memphant-core --lib`.

| target | what it covers |
|---|---|
| `memphant-eval/tests/syndai_trace_compare.rs` | the 5 spec-28 Syndai fixtures, **including the adversarial one that caught this** |
| `memphant-eval/tests/eval_contract.rs` | `examples/evals/golden.yaml` + the 12 `examples/evals/golden/*.yaml` oracle cases (2 of them packing-abstention), and the rung4/5/6/7/10/11/12/15 sampled-bank lever deltas — `rung7_state_style_suite_proves_packing_abstention_delta` is a *packing* delta |
| `memphant-eval/tests/profile_contract.rs` | `rung7-packing-abstention-profile.yaml` and the other promotion profiles |
| `memphant-core/tests/recall_trace_golden.rs` | 14 end-to-end recall tests, incl. `packing_collapses_duplicate_decoys_and_preserves_answer_under_budget`, `packing_abstains_when_top_evidence_is_unresolved_contradiction`, `packing_does_not_abstain_for_resolved_supersedence_edge`, `dsr_decay_fold_promotes_reinforced_memory_over_ignored_stale_candidate`, `recall_golden_fixtures_pass` |
| `memphant-core/tests/candidate_pool.rs`, `recall_pool_depth.rs` | the pool/scan-window seam `03fa1266`'s branch became reachable through |
| `memphant-core/tests/cross_reranker.rs` | the arm where `rank_based_ordering_active` used to be true — the only arm the 2026-07-12 gate verdict ever observed |
| `memphant-core/tests/quantity_rollup.rs`, `chunk_span_invariant_repro.rs`, `contextual_chunk_write.rs`, `temporal_grounding.rs`, `embedding_channel.rs`, `bitemporal_recall.rs` | other channels and pack-render paths a fusion or packing edit moves |
| `memphant-cli/tests/compile_contract.rs`, `memphant-core/tests/write_compiler_golden.rs` | golden-corpus consumers of the same fixtures |

### 5.3 Recommended standard check for this program

1. **`cargo test --workspace --no-fail-fast` is the floor**, not
   `-p <crate> --lib`. It is free, needs no database, and would have caught this
   in one command. Any narrower invocation must be justified in the build log.
2. **Attribute every failure to the commit that introduced it, not to your
   base.** `git bisect run cargo test -p <crate> --test <t>` between trunk and
   the branch point. Never write "pre-existing" without naming a revision that
   predates the program's own work.
3. **A packing or ranking change additionally runs both banked instruments** —
   Track R (code lane) and LME-S n=178 (chat lane). The packing fix did this and
   it was right to.
4. **The Postgres leg** (`scripts/with_scratch_db.sh … -p memphant-store-postgres
   -p memphant-worker -- --ignored --test-threads=1`) for anything touching the
   store, roles, or the worker.

### 5.4 A second, unrelated regression found on the way — the worker drain is broken at HEAD

Running the Track R harness against binaries built from `af-packadj` HEAD failed
at `compiled job count mismatch: 256 != 21370 events` — exactly one batch
(`MEMPHANT_WORKER_BATCH_SIZE=256`) and then a clean exit. Cause, proven against
a scratch database:

```
current_user=memphant_worker
current_tenant_id_is_null=true
job_state_forcerowsecurity=true
policy=(tenant_id = memphant.current_tenant_id())
```

Since W3.3 (`0f401dea`, merged `7f450c5d`) the worker pool issues
`SET ROLE memphant_worker`. `memphant.job_state` has FORCE RLS with a policy
keyed on `memphant.current_tenant_id()`, which is NULL on a pool-level session.
`PgStore::pending_worker_job_count` is an **unscoped** `select count(*) from
memphant.job_state` on `self.pool` — so it returns 0 whatever the queue holds —
while `claim_reflect_jobs` is `SECURITY DEFINER` and still claims real work.
`drain_finished(pending = 0, …)` therefore breaks the loop after the first
batch.

**Why no test caught it:** the one drain test,
`memphant-worker/tests/worker_once.rs::worker_drain_exits_zero_and_prints_exactly_one_summary_line`,
calls `clear_pending_worker_jobs` first and then asserts only exit-0 and a
single well-formed summary line. It drains an **empty** queue. A drain that
stops early on a full queue passes it unchanged. That test should seed a queue
larger than one batch and assert the queue is empty afterwards.

This is **not** caused by the change in this document and is **not** fixed here
— it is a store/roles defect that needs its own owner. It forces every Track R
measurement to be taken from a base that predates it; see §4.

**Nobody has yet run Track R on a W3.3-containing build.** The render-loss
completion pass measured `ccaa9e1c` → `f67f2b2a`, both of which predate
`7f450c5d`; this work measured the same way for the same reason. So the
committed 168/180 figure, and the one below, are both from the pre-W3.3
lineage. Until the drain is repaired, **the coding lane cannot be measured on
trunk at all** — which is a more serious instrumentation fact than any single
number in this document.

### 5.5 A third red test, also merged rather than caught

`memphant-core/tests/contextual_chunk_write.rs::recall_chunk_renders_matched_window_plus_neighbour`
is red on `accuracy-first` tip (`c58d4eac`). Bisected by checkout:

| revision | result |
|---|---|
| `ccaa9e1c` (before the render-loss fix) | 5 passed, **0 failed** — `ok` |
| `f67f2b2a` (the render-loss fix) | **FAILED** |
| `c58d4eac` (`accuracy-first` tip) | **FAILED** |

Introduced by `f67f2b2a`, which is exactly the class of test that change should
have been checked against — a chunk-render assertion, in the crate it edits, in
`crates/memphant-core/tests/`. Same shape of miss as §5.1, on a third
independent branch. Not fixed here; it belongs to the render-loss owner. It
does mean the claim that `syndai_trace_compare` is "the only red test on the
integration branch" is not correct — there are two.

## 6. Reproduce

```sh
# (a) the regression is ours — verify at the pre-fix revision, by checkout
git worktree add /tmp/a96 a96c289c
cd /tmp/a96 && cargo test --workspace --no-fail-fast     # 664 passed, 0 failed

# (b) the fixture, and the ranking-stage unit of it
cargo test -p memphant-eval --test syndai_trace_compare
cargo test -p memphant-core --test recall_trace_golden \
  keyword_stuffed_body_does_not_outrank_a_fully_covered_subject_key

# (c) per-candidate channel scores (§1/§2) — env-gated dumps, applied to a
#     scratch copy of memphant-core; not committed. Two probes:
#       PACKADJ_DUMP=1  in pack_recall_context, before the fill loop:
#         fused_score / exact_score / lexical_score / token_set_overlap_score /
#         decay.retrievability / packing_relevance_score per candidate
#       PACKADJ_CHAN=1  in the channel loop, before the rank assignment:
#         per-pass score at {:.17e} for every unit, all three LexicalScorer arms

# (d) the two rejected options, each measured against 3fc4eede^
#       EXACT_CHANNEL_WEIGHT 1.0 -> 3.0        => missing=["mem_rollout_task_state"]
#       exact_score tie-break before the body  => missing=["mem_error_budget_constraint"]

# (e) Track R, both arms, from the 168/180 lineage (~65 min each, run in parallel)
shasum -a 256 benchmarks/data/track_r_repo_memory_golden.jsonl   # 6f549daa…
shasum -a 256 docs/build-log/artifacts/track-r/corpus.jsonl      # c008142e…
git worktree add /tmp/pa-base f67f2b2a
git worktree add -b tmp-pa-fix /tmp/pa-fix f67f2b2a && \
  git -C /tmp/pa-fix cherry-pick 3fc4eede
for w in /tmp/pa-base /tmp/pa-fix; do
  ( cd $w && cargo build --release \
      -p memphant-server -p memphant-worker -p memphant-cli \
      -p memphant-eval --features fastembed )
done
PYTHONPATH=. python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence  docs/build-log/artifacts/track-r/packadj2/<arm>-bm25code-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/packadj2/<arm>-bm25code-provenance.json \
  --embed-model off --mode fast --k 10 --budget-tokens 8192 --lexical-scorer bm25-code \
  --label packadj2-<arm> --port <fresh> \
  --server-bin /tmp/pa-<arm>/target/release/memphant-server \
  --worker-bin /tmp/pa-<arm>/target/release/memphant-worker \
  --cli-bin    /tmp/pa-<arm>/target/release/memphant-cli

PYTHONPATH=. python3 scripts/analyze_pack_displacement.py \
  --before docs/build-log/artifacts/track-r/packadj2/base-bm25code-provenance.json \
  --after  docs/build-log/artifacts/track-r/packadj2/fix-bm25code-provenance.json \
  --out    docs/build-log/artifacts/track-r/track_r_packadj_exact_magnitude.json

# (f) chat lane, both arms
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant LME_DB \
  sh -c '/tmp/pa-<arm>/target/release/memphant-eval bench-lme --database-url "$LME_DB" \
    --data benchmarks/data/longmemeval_s.development.json \
    --sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64 --embed-model small \
    --emit-qa …/packadj2/chat-<arm>-evidence.jsonl \
    --out     …/packadj2/chat-<arm>-retrieval.json'

# (g) the drain defect of §5.4, proven without any data
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant RLSDB \
  sh -c 'psql -tA "$RLSDB" -c "set role memphant_worker;
    select current_user, memphant.current_tenant_id() is null;
    select relforcerowsecurity from pg_class where oid=''memphant.job_state''::regclass;
    select pg_get_expr(polqual, polrelid) from pg_policy
      where polrelid=''memphant.job_state''::regclass;"'
```

## 7. What this does and does not establish

**Established.** The fixture's failure is a ranking defect, its mechanism is
named and measured to the last decimal, better lexical scoring does not touch
it, and the repair is inert on both banked instruments — one of them
byte-identically.

**Not established.** That the repair *helps* anywhere. Both instruments are
blind to the Exact channel because both ingest raw episodes, whose `fact_key`s
are auto-derived UUID hashes. The only evidence that subject-key coverage
matters at all is the fixture itself, which is a four-unit adversarial
construct with hand-written subject keys. A `retain`-shaped corpus — units with
real `fact_key`s, the surface this channel exists for — is not measured by
anything this program currently owns. That is the gap the coverage audit's
recommendation cannot close, because no such instrument exists yet.

So: this is a correctness repair with a proven-null blast radius, not an
accuracy win, and it should not be counted as one.

## 8. Provenance

Commits on `af-packadj`, none pushed:

- `3fc4eede` — `fix(rank): scale the Exact channel by its own subject-key coverage`
  (the change, plus its ranking-stage regression test)
- `2ebf0f2f` — merge `accuracy-first` (render-loss completion pass), to rebaseline
  the Track R comparison onto 168/180
- `a83981ac` — merge `accuracy-first` (worker drain RLS fix, instrument register)

Measurement worktrees, both from `f67f2b2a`: `/tmp/pa-base` and `/tmp/pa-fix`
(`f67f2b2a` + cherry-picked `3fc4eede`).

Committed artifacts:
`docs/build-log/artifacts/track-r/track_r_packadj_exact_magnitude.json`,
`docs/build-log/artifacts/rung7-packing-reader-gate/packadj2/chat-lane-nonregression.json`.
Per-question Track R outputs under `…/track-r/packadj2/` are gitignored (they
carry third-party event bodies), as are the LME-S per-question evidence files.

Not touched, per standing instruction: the parked v5 campaign census (its test
now skips on its own) and the terminal SWE-ContextBench rehearsal.
