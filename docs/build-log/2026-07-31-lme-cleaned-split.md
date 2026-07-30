# W0.3 — the LongMemEval cleaned split, pinned and measured (2026-07-31)

Cost: **$0 paid API spend.** Both arms are retrieval-only with a local embedder
(`fastembed:bge-small-en-v1.5`, 129 MB cache). No reader, no judge, no provider
key read, no paid model call. Reader QA was **not** run — it is paid and
separately preregistered.

No checkbox, default, cutover, or SOTA claim moves in this document.

## Headline

**The standing chat-lane R@k does not move.** On the same 100 sampled questions,
the same seed, and byte-identical harness settings, the cleaned split scores
**0.6170** against the deprecated split's **0.6277** at both k=5 and k=10 — a
delta of **−0.0106**, which is **exactly one question** out of 94 scored. Exact
two-sided McNemar **p = 1.0** (0 arm-only wins, 1 baseline-only win); seeded
bootstrap 95% CI on the paired delta **[−0.0319, 0.0000]**.

The pairing is total: **all 500 question IDs join across the two splits**, with
no drops, no renames, and no changes to question text, question type, answer, or
`answer_session_ids`. The sample is therefore the identical 100 questions in
both arms, not two independent draws.

The null has a structural cause, and it is the real finding of this task:

> **The LME-S "cleaning" is de-padding, not a content change.** It removes 1,243
> haystack sessions, of which **1,230 are empty** (zero turns). Total haystack
> turns fall from 246,930 to 246,750 — **−0.07 %**. Every one of the 23,854
> retained sessions is **byte-identical**, and no session was added.

A corpus that loses 0.07 % of its turns cannot move a retrieval number, and it
did not.

## 1. Scope correction — most of the lane was already on the cleaned split

The task premise was that our standing numbers were measured on the deprecated
split. That is true, but only of one cohort, and the repo had already migrated:

| pin | dataset sha256 | where it is used |
|---|---|---|
| `xiaowu0162/longmemeval` (deprecated) | `08d8dad4…` | **35 committed artifacts**, all from the 2026-07-10/11 retrieval + wave campaigns — this is where the R@10 ≈ 0.83–0.94 headline figures live |
| `xiaowu0162/longmemeval-cleaned` | `d6f21ea9…` | the fetcher's pin since **0af44ad4** (2026-07-19); `p1-retrieval-bench/t2-ab` runs |
| `longmemeval_s.development.json` (178-q dev cohort) | `e4667bed…` | rung-7 packing, A1 fast-miss, phase1d — **derived from the cleaned split** |

The 178-question development cohort is a *derived slice of the cleaned split*:
`scripts/build_lme_reader_controls.py` hard-fails unless the parent file's
sha256 equals `benchmarks/manifests/longmemeval_s.split.json`'s
`dataset.sha256`, which is `d6f21ea9…`. So the exposure-guarded dev and
confirmation lattices — and every rung-7/A1 number measured on them — are
already cleaned-split numbers and were never at risk.

What was on the deprecated split is the **2026-07-10/11 wave**, which is exactly
the cohort the R@10 ≈ 0.83–0.94 range comes from. This document measures whether
that cohort's split choice was a confound. It was not.

## 2. What the cleaning actually did

Measured directly from the two pinned bodies (`corpus_shape()` in the
materializer; the same figures are asserted in the lock and re-derived by
`--verify-lock`):

| property | deprecated `08d8dad4…` | cleaned `d6f21ea9…` | delta |
|---|---:|---:|---:|
| questions | 500 | 500 | 0 |
| unique question IDs | 500 | 500 | 0 |
| haystack sessions | 25,112 | 23,867 | −1,245 |
| **empty haystack sessions** | **1,230** | **0** | **−1,230** |
| haystack turns | 246,930 | 246,750 | −180 (−0.07 %) |
| answer sessions | 948 | 948 | 0 |

Session-level diff, matched by session ID within each question:

- **1,243 sessions removed**, carrying **180 turns** between them — i.e. 1,230
  were empty and 13 held content.
- **0 sessions added.**
- **23,854 retained sessions are byte-identical**; 0 changed.

(The 1,245-vs-1,243 gap is duplicate session IDs *within* a single question's
haystack — 15 in the deprecated split, 13 in the cleaned one. Present in both,
upstream, not introduced by the cleaning.)

Question-level parity across the two splits, all 500 IDs:

- `question_type` mismatches: **0**
- `question` text mismatches: **0**
- `answer` mismatches: **0**
- `answer_session_ids` mismatches: **0**

Because IDs and question types are identical, the harness's stratified seeded
sample (`stratified_sample_ids`, proportional allocation then a seeded
Fisher-Yates inside each stratum sorted by question ID) selects the **same 100
questions** in both arms. The pairing is a property of the data, not an
assumption — `scripts/compare_lme_split_recall.py` reports the join explicitly
so a future split that drops or renames questions shows up as a shrunken pair
set rather than a silently different denominator.

## 3. The retrieval-only comparison

Both arms: fresh release binary built in this worktree, fresh scratch DB via
`scripts/with_scratch_db.sh` (auto-dropped), retrieval-only, zero degraded
questions.

### Harness settings — identical in both arms, asserted not assumed

`compare_lme_split_recall.py` compares 19 recorded settings between the two
reports and lists any that differ. **`harness_settings_mismatched: []`.**

| setting | value |
|---|---|
| sample_n / sample_seed | 100 / 20260710 |
| k | 10 |
| embed model | `small` → `fastembed:bge-small-en-v1.5`, 384 dims |
| granularity / turns_window | `session` / 4 |
| budget_tokens | 8192 |
| recall_pool_depth (`--pool`) | 64 |
| mode | `fast` |
| lexical_scorer | `overlap` |
| cross_rerank | false |
| sibling_gather / session_quota / pack_render_cap | false / null / null |
| temporal_grounding / fact_extraction | false / false |
| runtime_chunks | true |
| retrieval_only | true |
| runtime | postgres |

### Results

| arm | dataset sha256 | sessions ingested | n scored | R@5 | R@10 | degraded |
|---|---|---:|---:|---:|---:|---:|
| deprecated (baseline) | `08d8dad4…` | 4,935 | 94 | **0.6277** (59/94) | **0.6277** (59/94) | 0 |
| **cleaned** | `d6f21ea9…` | 4,705 | 94 | **0.6170** (58/94) | **0.6170** (58/94) | 0 |

| paired comparison (n = 100 joined, 94 scored) | k=5 | k=10 |
|---|---|---|
| delta (cleaned − deprecated) | **−0.0106** | **−0.0106** |
| both hit / both miss | 58 / 35 | 58 / 35 |
| cleaned-only wins / deprecated-only wins | **0 / 1** | **0 / 1** |
| exact two-sided McNemar p | **1.0 — null** | **1.0 — null** |
| bootstrap 95 % CI on paired delta (10 000 resamples, seed 20260731) | **[−0.0319, 0.0000]** | **[−0.0319, 0.0000]** |

The single discordant question at both k is **`gpt4_6ed717ea`** (temporal-reasoning),
hit on the deprecated split and missed on the cleaned one.

Six of the 100 sampled questions are abstention questions, excluded from recall
by the harness and scored separately: **3/6 correct in both arms**, unchanged.

R@5 equals R@10 in both arms. That is the known rung-7 packing behaviour (the
pack is budget-bound well before rank 10), not an artifact of this comparison —
it reproduces on both splits identically.

### What this comparison does not claim

The absolute **0.6277** here is **not** a reproduction of the published
`lme-wave-base` **0.7979** at the same seed and sample. That report ran
`recall_pool_depth=32` (the legacy vector-channel fan-out, recorded then as
`candidate_pool_size`) and `--disable rerank`; these arms run today's defaults
(`--pool 64`, nothing disabled). Both arms here share one setting vector, which
is what licenses reading the split delta — but the delta is the claim, not the
absolute level. Re-earning the 2026-07-10 absolute figures under today's
defaults is a separate run and is not done here.

Power is bounded by the sample: with 94 scored questions and one discordant
pair, the interval above still admits a true degradation of up to ~3.2 points.
It excludes anything larger. Given §2 — a corpus that differs by 0.07 % of its
turns — a larger effect would have been the surprising result.

## 4. The pin

`benchmarks/manifests/longmemeval_s.lock.json` already named the cleaned repo
and revision. It now also records what a lock in this repo is supposed to record:

- **dataset id**: `xiaowu0162/longmemeval-cleaned`
- **revision**: `98d7416c24c778c2fee6e6f3006e7a073259d48f` (commit sha, never `main`)
- **license**: `mit` (both repos, from HF dataset-card `cardData.license`)
- **file sha256 + bytes**: `longmemeval_s` `d6f21ea9…` / 277,383,467 B;
  `longmemeval_oracle` `821a2034…` / 15,388,478 B
- **row counts**: the full `shape` block per file (questions, unique IDs,
  haystack sessions, empty sessions, turns, answer sessions)
- **materializer sha256**: `scripts/fetch_longmemeval.py` at
  `81049b67b20bcf80caca75b9ceebbc6da9d6470959fce0bacfc7c4c5423b6f74`
- **mirror**: `~/.memphant-private/longmemeval-cleaned/`, filenames matching the
  `benchmarks/data/` basenames, same sha256s

The deprecated split is pinned too, under `deprecated_split`, at commit
`2ec2a557f339b6c0369619b1ed5793734cc87533`. Its first pin (2026-07-10) resolved
`main`, which is not a pin at all. The named commit's body hashes to
`08d8dad4be43…` — **byte-identical to the file every standing chat-lane number
was measured on** — so this comparison reproduces rather than approximates.

Bodies stay gitignored. The mirror exists because gitignored-and-single-copy is
how this repo already lost a 64k-event corpus; `--verify-lock` fails if a
mirrored copy is absent or its hash drifts.

### `--verify-lock`

`python3 scripts/fetch_longmemeval.py --verify-lock` runs **no network I/O** and
re-derives every figure the lock asserts: file sha256s, byte counts, the
row/session/turn shape, the materializer's own sha256, and the mirror's copies.
It exits non-zero on the first disagreement.

It was negative-tested, because a check that never fails is not a check.
Corrupting the materializer sha, a shape count, and the deprecated split's
sha256 produced four `FAIL` lines and exit 1; restoring produced `OK` and exit 0.

## 5. Provenance and honesty notes

- Every figure above comes from a run executed for this document. Artifact paths
  are in §6. Nothing is carried over from an earlier campaign except the
  explicitly-labelled published `0.7979` in §3, which is cited to show a
  *difference* in settings, not reused as evidence.
- **Worker drain.** Both arms report `degraded: 0`. A mid-run snapshot of the
  deprecated arm's scratch DB showed `job_state` = {`done`: 4920, `running`: 15}
  — **zero pending, zero dead**. A post-run snapshot is not available because
  `with_scratch_db.sh` drops the database on exit; the drain evidence is the
  harness's synchronous reflect path plus `degraded: 0` on all 200 questions.
- Both scratch databases auto-dropped. Each arm minted its own.
- The comparison's bootstrap is seeded (`20260731`, 10 000 resamples) and was
  confirmed byte-reproducible across two invocations.
- `benchmarks/manifests/longmemeval_s.split.json` is **not** modified by this
  work. The fetcher rewrites its exposure snapshot as a side effect; that drift
  (git commit + tracked-artifact counts, cohorts unchanged) was reverted. Never
  bump a split constant.
- The exact McNemar implementation reproduces all nine paired p-values published
  in `2026-07-30-phase1r-retrieval-bm25.md` to their printed precision.

## 6. Artifacts

| what | path |
|---|---|
| cleaned arm report | `docs/build-log/artifacts/lme-cleaned-split/cleaned-n100-seed20260710.json` |
| deprecated arm report | `docs/build-log/artifacts/lme-cleaned-split/deprecated-n100-seed20260710.json` |
| paired comparison | `docs/build-log/artifacts/lme-cleaned-split/paired-comparison-n100-seed20260710.json` |
| lock | `benchmarks/manifests/longmemeval_s.lock.json` |
| materializer / verifier | `scripts/fetch_longmemeval.py` |
| comparison tool | `scripts/compare_lme_split_recall.py` |
| private mirror | `~/.memphant-private/longmemeval-cleaned/` (not committed) |

## 7. Reproduce

```sh
cd /Users/sidsharma/Memphant-af-w0-instrument      # branch af-w0-instrument

# 1. Fetch both pinned bodies (gitignored) and mirror them. Verifies sha256
#    before replacing anything; never changes a pin.
python3 scripts/fetch_longmemeval.py --deprecated

# 2. Re-derive every figure the lock asserts. No network.
python3 scripts/fetch_longmemeval.py --verify-lock

# 3. Build the binary IN THIS WORKTREE.
cargo build --release -p memphant-eval --features fastembed

# 4. The two arms. Each mints and drops its own scratch DB; run them
#    sequentially, or stagger by ~60 s (concurrent scratch-DB migrations race
#    on `tuple concurrently updated`). ~25 min each on a loaded machine.
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant \
  MEMPHANT_LME_DB \
  bash -c 'target/release/memphant-eval bench-lme --database-url "$MEMPHANT_LME_DB" \
    --data benchmarks/data/longmemeval_s.json \
    --sample 100 --seed 20260710 --k 10 --embed-model small \
    --out docs/build-log/artifacts/lme-cleaned-split/cleaned-n100-seed20260710.json'

bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant \
  MEMPHANT_LME_DB \
  bash -c 'target/release/memphant-eval bench-lme --database-url "$MEMPHANT_LME_DB" \
    --data benchmarks/data/longmemeval_s_original_deprecated.json \
    --sample 100 --seed 20260710 --k 10 --embed-model small \
    --out docs/build-log/artifacts/lme-cleaned-split/deprecated-n100-seed20260710.json'

# 5. The paired comparison.
python3 scripts/compare_lme_split_recall.py \
  --baseline docs/build-log/artifacts/lme-cleaned-split/deprecated-n100-seed20260710.json \
  --arm docs/build-log/artifacts/lme-cleaned-split/cleaned-n100-seed20260710.json \
  --baseline-label deprecated-08d8dad4 --arm-label cleaned-d6f21ea9 \
  --out docs/build-log/artifacts/lme-cleaned-split/paired-comparison-n100-seed20260710.json
```

## 8. Recommendations (owner decisions, not taken here)

1. **Do not re-run the 2026-07-10/11 wave for split reasons.** The split is not
   a confound: 0.07 % of turns differ and the paired delta is one question at
   p = 1.0. If that cohort is ever re-run, it should be to re-earn it under
   today's defaults (§3), which is a different and larger question.
2. **Keep citing the cleaned split as the lane's corpus**, which it already has
   been since 0af44ad4. The deprecated pin is retained only so this comparison
   reproduces; nothing should be measured on it going forward.
3. **Reader QA was not run and remains the open item.** The standing 0.56 is the
   n=100 2026-07-10/11 wave figure (`2026-07-10-runtime-chunks-campaign.md`:
   "R@10 was 0.83 while QA was 0.56"), and that cohort **is** on the deprecated
   split — unlike the 178-question dev cohort's reader work, which is already
   cleaned-split-derived. This document shows the *retrieval* input to that
   reader is unchanged by the split, which makes a split-driven reader shift
   unlikely, but **that is an inference, not a measurement**. Reader QA is paid
   and separately preregistered; if the owner wants the 0.56 re-earned on the
   cleaned split, it needs its own authorized run.
