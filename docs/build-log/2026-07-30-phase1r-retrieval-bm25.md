# Phase 1r — closing the code-lane retrieval gap (2026-07-30)

Cost: **$0 paid API spend.** Every arm is deterministic retrieval — no reader,
no judge, no paid model call, local embedders only. Answers
`docs/build-log/2026-07-30-phase1-golden-banks-and-retrieval-probe.md` §5(a) and
§6.4.

No checkbox, default, cutover, deployment, or SOTA claim moves in this
document. Nothing here is publishable: the Track R spot-check state is still
`emitted_pending_owner_review`. The lever ships **default OFF**, and §7 records
a pinned-provenance collision that must be adjudicated before it could ship at
all.

## Headline

On the same 180 goldens, the same attempt-scoped haystack, and the same stage
Phase 1c used, **MemPhant now beats the scoped BM25 control at both k**:

| arm | embeddings | r@5 | r@10 | paired @5 vs BM25 | paired @10 vs BM25 |
|---|---|---:|---:|---|---|
| scoped BM25 control | — | 0.8278 | 0.8944 | — | — |
| `overlap` (today's default) | off | 0.6278 | 0.8167 | 14/50, **p = 0.00001** ✗ | 14/28, **p = 0.0436** ✗ |
| `bm25-control` | off | 0.8722 | 0.9056 | 10/2, **p = 0.0386** ✓ | 5/3, p = 0.727 — null |
| **`bm25-code`** | **off** | **0.9500** | **0.9611** | 24/2, **p = 0.00001** ✓ | 15/3, **p = 0.0075** ✓ |
| `overlap` + dense | small | 0.7778 | 0.9000 | 15/24, p = 0.200 — null | 16/15, p = 1.000 — null |
| `bm25-code` + dense | small | 0.9111 | 0.9556 | 24/9, **p = 0.0135** ✓ | 17/6, **p = 0.0347** ✓ |

(Paired counts are arm-only/control-only; p is the **exact** two-sided McNemar
on the discordant pairs, not the chi-square approximation.)

The best arm is `bm25-code` with **embeddings off** — 171/180 at k=5 and
173/180 at k=10, gold at rank 1 on **135/180** against the control's 91,
leading every question shape at both k, and **3 short of the 176/180 policy
ceiling** established in §4. It is also the cheapest arm: no embedding model, no
vector column, no extra ingest time.

Phase 1c's localization was exactly right — "lexical matching is currently the
stronger signal, and fusion is diluting it."

## 1. The baseline reproduces exactly, which is what licenses reading the deltas

Arm 0 is a fresh full ingest, new worktree, new scratch DB, today's default
scorer. It reproduces the committed Phase 1c cap-off arm **exactly**: fused r@5
**0.6278** (113/180) and r@10 **0.8167** (147/180); gold in pool **176**; gold at
rank 1 **59**; packed buckets `absent_from_pool 4 / hit 91 / in_pool_unpacked
85`; paired vs scoped BM25 14/50 (p = 0.00001) at k=5 and 14/28 (p = 0.0436) at
k=10 — every figure identical to the Phase 1 build log.

The scoped BM25 control was also re-run from scratch, and its **per-question hit
vector is byte-identical** to the committed
`track_r_phase1c_scoped_bm25_comparison.json`.

Two independent reproductions, so the four new arms are deltas against a
verified baseline, not a new construct — the failure mode Phase 1 §3 exists to
prevent. Every arm ingested 64,055 events + 1 isolation sentinel with
`compiled=64056`, `pending_jobs=0`, `dead_jobs=0`; the worker fully drained in
all five, so no figure is a half-drain artifact. All scratch databases
auto-dropped.

## 2. What changed — one lever, default OFF

`MEMPHANT_LEXICAL_SCORER` (`LexicalScorer`, threaded construction-time exactly
like `PackLevers` — no `RecallRequest`, wire, or OpenAPI field) replaces the
fusion's **lexical family** — today's two token-overlap passes — with **one
Okapi BM25 pass** over the recall candidate pool, at the combined channel weight
of the two passes it replaces, still traced as `RecallChannel::Lexical`. Off, the
path is byte-identical to today.

Why the two overlap passes lose on code:

- `lexical_score` is `matched_tokens / document_length`; `token_set_overlap_score`
  is Jaccard over the token union. **Neither carries IDF**, so matching `the`
  counts exactly as much as matching `FIRESTORE_EMULATOR_HOST`.
- Both divide by a length-like denominator far more aggressively than BM25's
  `b`-normalization, so a rare identifier buried in a long tool result loses to a
  short body sharing a common word. Code-lane gold *is* long tool results.
- `tokens_related` treats any two tokens sharing a 5-character prefix as equal,
  which on identifier-dense text is a false-positive generator.

`k1 = 1.2`, `b = 0.75` and the `bm25-control` tokenization (`[a-z0-9_./-]+` over
lowercased text) are taken from the repo's own control at
`scripts/code_lane_run_deterministic.py`, so the arm is comparable to that
control by construction rather than by a second calibration. `bm25-code` adds
each token's alphanumeric sub-tokens, so `src/foo/bar.py` matches whole *and* by
directory, and IDF decides which is worth more.

**No parameters were swept.** `k1`/`b` were inherited from the control, and the
BM25 channel weight is the arithmetic sum of the two weights it replaces
(1.0 + 2.0). An exploratory offline bed over an approximate haystack was used to
choose *which* design to implement — it is not a reportable measurement and no
number from it appears here.

**Adapted third-party code: none.** BM25 is the textbook Robertson/Spärck-Jones
formulation (~25 lines) written against this repo's own control. The newly
permitted MIT/Apache-2.0/BSD adaptation allowance was not needed and not used, so
there is nothing to attribute.

## 3. Attribution — the three levers, separately

| lever | isolated as | Δ r@5 | Δ r@10 | verdict |
|---|---|---:|---:|---|
| **B** — BM25 instead of token overlap | `bm25-control` − `overlap` | **+0.2444** | **+0.0889** | works |
| **A′** — code-aware tokenization | `bm25-code` − `bm25-control` | **+0.0778** | **+0.0555** | works |
| **C** — dense embeddings | `dense+overlap` − `overlap` | +0.1500 | +0.0833 | works, but only up to a null vs BM25 |
| **C on top of B** | `dense+bm25-code` − `bm25-code` | **−0.0389** | **−0.0056** | does **not** work |

B and A′ are real and compose. B alone already beats the control at k=5
(p = 0.0386), but its k=10 result is an honest **null** (0.9056 vs 0.8944,
p = 0.727) — B alone is a k=5 win, not a k=10 win. Only the tokenizer change
makes k=10 significant.

### Lever C is a negative, stated plainly

Dense embeddings are a large, significant improvement **over the weak overlap
scorer** — paired vs `overlap`, +35/−8 at k=5 (p = 0.00004) and +21/−6 at k=10
(p = 0.0059). But that only carries it to a **null against BM25**: 0.7778
(p = 0.200) and 0.9000 (p = 1.000). *Nothing dense had ever been measured on this
lane; it has now been, and on its own it does not beat a plain BM25 control.*

Worse for the hybrid story: adding the dense channel **on top of** `bm25-code`
**costs** questions. Paired against `bm25-code`, `dense+bm25-code` is −10/+3 at
k=5 (p = 0.092) and −3/+2 at k=10 (p = 1.000). Neither loss is significant, so the
honest reading is *no evidence dense adds anything once BM25 is in, with a
negative point estimate at k=5* — not "dense hurts". Either way it does not earn
its cost: dense roughly triples ingest wall clock (~13 min of embedding-bound
worker drain per 64k events on this machine) for a result that is at best flat.

**Hybrid fusion is therefore not recommended on this lane.** Plain BM25 and
standard RRF were treated as baselines to beat, per the brief, and the
lexical-only arm is the one that beats them.

> **RETRACTED 2026-07-31 — do not act on the paragraph above.** Every dense
> comparison on this page was measured on the **original** Track R bank, which
> leaks lexically at **3.93×** (target 0.3960 / floor 0.1008, exhaustive) against
> a measured achievable floor of 1.79×. A bank that hands the query's vocabulary
> to the target is precisely the regime in which a dense channel has nothing left
> to add, so this null is an artifact of the instrument, not a property of the
> lane.
>
> On the **paraphrase** bank (leakage 2.018×, 180/180 adjudicated distractors,
> 19.45 mean withheld terms) the verdict inverts. `dense + bm25-code` vs
> `bm25-code` alone, fused@10: **b=29 / c=5, p = 3.86e-05**, fused hits
> **89 → 113**. Dense also carries the weak lexical scorer **40 → 83**, so it is
> doing real work in both lexical regimes rather than rescuing one bad
> configuration. See `docs/build-log/2026-07-31-null-review.md`
> (`w02-dense-paraphrase-at10`, `w02-hybrid-vs-bm25code-para-at10`) and the W0.2
> arms at `3e2cc2ba`.
>
> The wall-clock objection survives and is unchanged: dense roughly triples
> ingest. That is now a **cost/benefit** question against a measured +23.9pt, not
> a reason to skip a channel that was believed to buy nothing.

## 4. Lever A as briefed — the Postgres text config — is measured inert, and it is not free

The brief proposed swapping the lexical prefilter's
`websearch_to_tsquery('english', …)` for `simple`. Three measurements say the
prefilter is not where the loss lives:

1. **The prefilter is very nearly a no-op on this lane.** MemPhant's candidate
   pool covers a **median 0.985** (mean 0.952) of the bound attempt's unique unit
   bodies, and **no attempt reaches the 200-row prefilter cap**. The FTS family is
   OR-joined and the most-recent-100-per-scope family backstops it.
2. **The residual 4 pool misses are not lexical at all.** All four have
   `pool_size = 0` — the *entire* attempt was dropped — with
   `drop_reasons = {below_trust_floor: N}`, **identically in all five arms**.
   Exactly four of the 180 queries trip `high_risk_action_query`, and they are
   exactly those four, on benign coding vocabulary: `create`+`claim`
   (`track_r_034`), `create`+`registry` (`track_r_072`) via the fraud pair, and
   `script`+`library` (`track_r_120`), `script`+`support` (`track_r_134`) via the
   cyber-abuse pair. So **176/180 is a policy ceiling**, not a tokenizer ceiling,
   and no text-search config could move it.
3. **The change is not free.** Measured on a scratch database holding the real
   **64,013** Track R unit bodies (the same per-attempt-deduped projection the
   runs ingest): `body_tsv` is a *generated stored* column, so the swap is
   `drop column` + `add column` + GIN rebuild — **8.0 s wall clock** under an
   ACCESS EXCLUSIVE lock at this size, with the table growing
   **128.9 MB → 149.6 MB (+16.0%)** because `simple` neither stems nor
   stopword-strips. That 8 s is a table-rewrite time and scales linearly with row
   count; at production scale it is a blocking migration needing
   new-column-plus-backfill-plus-swap, not an in-place `ALTER`.

**Lever A as briefed is therefore not taken.** Its measured ceiling on this bank
is zero questions. The tokenizer win it was reaching for is real, but it lives
in-process (lever A′), where it costs no migration at all.

The four safety-guard false positives are **recorded, not fixed**: weakening a
high-risk-action filter to raise a benchmark number is exactly the trade this
program should refuse, and the guard's phrasing is an owner decision.

## 5. Chat-lane non-regression — it improves

Retrieval-only LME-S, identical seed and identical pinned dataset in both arms,
paired per question, abstention cases excluded exactly as `bench_lme`'s own
recall excludes them:

| sample | graded n | `overlap` | `bm25-code` | paired | exact p |
|---|---:|---:|---:|---|---:|
| n = 30, seed 1 | 28 | 17 | 18 | +2/−1 | 1.000 |
| **n = 120, seed 1** | **111** | **66** | **75** | **+10/−1** | **0.0117** |

(k=5 and k=10 are identical in both arms, as they are for this harness.)

The shared retrieval path does not pay for the code-lane win — it gains. This is
a *control*, not a promotion basis: retrieval-only, no reader, no SLO re-run.

## 6. What did not move: packing

Packed r@10 across the arms is 0.5056 (`overlap`) → 0.5944 (`bm25-control`) →
0.5389 (`bm25-code`) → 0.5278 (dense) → 0.5833 (dense+`bm25-code`). Better
ranking raises the packed number, but **the packing stage still discards most of
the gain**: `bm25-code` puts gold in the fused top-10 on 173/180 and the packer
delivers 97. Phase 1 §5(b) stands untouched, and it is now the single largest
remaining loss on this lane. That stage is owned separately and nothing here
modifies it — `packing_relevance_score` still uses the overlap scorers, which is
what keeps the packing stage constant across all five arms.

## 7. Blocking: this change collides with two pinned-provenance artifacts

Adding a construction-time lever necessarily edits `MemoryService`, and the
harness lever necessarily edits the shared server runner. Both files are
sha256-pinned by committed provenance:

- `benchmarks/manifests/longmemeval_v2.state_aware_full.v{1..5}.json` pin
  `crates/memphant-core/src/service.rs` under `construction.code_sha256s`.
  `tests/test_run_lme_v2_state_aware.py::test_canonical_census_source_inventory_covers_declared_campaign_code`
  now fails with `census binary source identity drift`.
- A committed SWE-ContextBench rehearsal pins `scripts/gate_runtime.py`;
  `tests/test_swe_contextbench_memphant.py::test_committed_rehearsal_has_complete_receipts_and_runtime_identity`
  now fails on `gate_runtime_sha256`.

**Neither pin was edited.** Re-pinning a manifest to make a test green would
invalidate a live authorization chain — precisely what `AGENTS.md` forbids. This
is an owner decision: either re-pin with a recorded rationale as part of
promoting this lever, or hold the lever on this branch until the v5 campaign and
the rehearsal are closed.

Two further verification notes, both **pre-existing and not caused by this
work**, confirmed by re-running at the branch point `a96c289c`:
`scripts/check_spec_drift.py` reports `dirty` on `STATUS.md`,
`13-prior-art-and-competitive-spec.md` and `26-decision-register.md` (the private
Syndai mirror is ahead), and
`tests/test_public_launch_gate.py::test_public_sota_claim_policy_is_explicit_and_bare_claims_are_guarded`
fails because `playwright` is not installed in this environment.

Everything else is green: `cargo fmt --check`, `cargo clippy --workspace
--all-targets --all-features -D warnings`, `cargo test --all-targets
--all-features` (73 suites, 0 failures), `cargo test --doc`, `db lint` clean for
`plain-postgres`/`supabase`/`neon`, and `python3 -m pytest tests/ -q` →
1027 passed with only the four failures accounted for above.

## Provenance

Commits on `af-retrieval`, none pushed: `6f785957` (BM25 lexical scorer) →
`d60ba6d0` (paired comparator + `bench-lme --lexical-scorer`) → `bd609d37`
(three-arm measurement) → `064de52a` (LME-S non-regression).

Committed artifacts:
`docs/build-log/artifacts/track-r/track_r_phase1r_retrieval_arms.json` and
`…/track_r_phase1r_lme_s_nonregression.json`. Per-arm evidence and provenance
under `…/track-r/phase1r/` are gitignored under the same rule as Phase 1 — the
evidence JSONLs carry third-party event bodies.

Input contract verified before every run: corpus sha256
`c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669`, golden bank
sha256 `6f549daaa3cc5be6dae095d044a50d17a8fd4ab82a23f2e973901cbb52a89b6d`.

### Reproduce

```sh
cd /Users/sidsharma/Memphant-af-retrieval            # branch af-retrieval
cargo build --release -p memphant-server -p memphant-worker -p memphant-cli

# scoped BM25 control (~4 s, no DB, no server, no model call)
python3 scripts/code_lane_run_deterministic.py \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1r/bm25-scoped-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1r/bm25-scoped-provenance.json \
  --k 10 --scope attempt

# one MemPhant arm; vary --lexical-scorer {omitted|bm25-control|bm25-code} and
# --embed-model {off|small}. Each arm mints and drops its own scratch DB; use a
# fresh --port per arm and stagger concurrent launches by ~60 s (simultaneous
# scratch-DB migrations race on `tuple concurrently updated`).
python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1r/armAB-bm25code-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1r/armAB-bm25code-provenance.json \
  --embed-model off --mode fast --k 10 --budget-tokens 8192 \
  --lexical-scorer bm25-code --label armAB-bm25code --port 39612 \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli

python3 scripts/track_r_retrieval_arm_compare.py \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --control docs/build-log/artifacts/track-r/phase1r/bm25-scoped-provenance.json \
  --arm arm0_overlap=docs/build-log/artifacts/track-r/phase1r/arm0-overlap-provenance.json \
  --arm armB_bm25_control=docs/build-log/artifacts/track-r/phase1r/armB-bm25control-provenance.json \
  --arm armAB_bm25_code=docs/build-log/artifacts/track-r/phase1r/armAB-bm25code-provenance.json \
  --arm armC_dense_overlap=docs/build-log/artifacts/track-r/phase1r/armC-dense-provenance.json \
  --arm armABC_dense_bm25_code=docs/build-log/artifacts/track-r/phase1r/armABC-dense-bm25code-provenance.json \
  --out docs/build-log/artifacts/track-r/track_r_phase1r_retrieval_arms.json

# chat-lane control (needs benchmarks/data/longmemeval_s.json and a warm
# .fastembed_cache); run once per --lexical-scorer arm
cargo build --release -p memphant-eval --features fastembed
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant \
  MEMPHANT_LME_DB \
  bash -c 'target/release/memphant-eval bench-lme --database-url "$MEMPHANT_LME_DB" \
    --data benchmarks/data/longmemeval_s.json --sample 120 --seed 1 --k 10 \
    --lexical-scorer bm25-code \
    --out docs/build-log/artifacts/track-r/phase1r/lme-s-n120-bm25code.json'
```

## Recommendations (owner decisions, not taken here)

1. **`bm25-code` is the arm to promote**, if the §7 pin collision is resolved.
   It beats the control at both k, improves the chat lane, and costs nothing at
   ingest. Promotion still needs the SLO harness re-run and a reader-token delta
   per the program's own default-flip rule; neither was run here.
2. **Do not build hybrid dense fusion on this lane.** §3 is a measured negative.
3. **Packing is now the largest loss** on the code lane (173 fused → 97 packed).
4. **Adjudicate the four safety-guard false positives** as a separate,
   non-benchmark question. Four benign coding queries currently blank the entire
   recall.
