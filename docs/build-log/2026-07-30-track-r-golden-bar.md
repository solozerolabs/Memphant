# Track R repo-memory golden bank — preregistered quality bar

Date: 2026-07-30
Phase: accuracy-first program, Phase 1a-R
Status: **preregistered** — committed before the miner was run. Any number in
`benchmarks/data/track_r_repo_memory_golden.lock.json` that fails a bar below
means the bank does not ship. The bar is not revisable downward after mining;
a failed bar is a kill-gate report, not a reason to relax a threshold.

## Why a bar exists at all

The v3 candidate bank was rejected
(`docs/build-log/artifacts/c3-public-code-lane-v3/rejection-receipt.json`) for
three recorded reasons:

1. the source text did not causally or semantically identify the selected target
   event;
2. non-target events that could plausibly answer were never adjudicated as
   distractors;
3. 19 of 40 answers were path-varied instances of one generic
   file-not-found template.

Each failure mode gets a **mechanical** check below, not a promise. Every check
is computed by `scripts/track_r_mine.py` and its outcome is recorded in the lock
file, including the count of rejects by reason.

## Corpus (pinned before mining)

- Source: `nebius/SWE-rebench-openhands-trajectories`, revision
  `35455389ab51bf5e2306bfd436ef72d0f98bf882`, license CC-BY-4.0.
- Materialized with the proven adapter `scripts/materialize_public_code_lane.py`
  (transform `openhands_trajectory_to_syndai_content_events_v2`, 4000-char event
  clip): 495 attempts / 330 repositories / 64,055 content events, corpus
  sha256 `c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669`.
- Classification: public synthetic agent rollouts over real issues. **Never**
  describable as organic production traffic.
- Bodies (corpus + mined goldens + spot-check sample) are gitignored. The lock
  file is the only committed artifact.

## Shapes

Three repo-memory shapes, each with a distinct candidate precondition:

| Shape | Target-event precondition |
|---|---|
| `state-churn` | the same concrete file path is touched at ≥2 separated points in the attempt; the target is a **later** touch, so a stale-state answer is wrong |
| `file-symbol-grounding` | the target event carries a concrete file path **and** a code symbol (function/class/identifier) from that repository |
| `task-resumption` | the target sits in the last 40% of the attempt and records an unresolved diagnostic or a pending next step |

## The bar (all thresholds binding)

### Size and composition

| Metric | Bar |
|---|---|
| Shipped goldens | **150–200** (fail below 150) |
| Per-shape minimum | **≥40** each of the three shapes |
| Distinct source attempts | **≥50** |
| Goldens per source attempt | **≤3** |
| Goldens per repository | **≤4** |

### Failure mode 1 — causal identification

| Metric | Bar |
|---|---|
| Goldens passing the identification check | **100%** (it is a hard reject) |
| Distinguishing tokens from the target present in the question | **≥2** per golden |
| Corpus-wide candidate set narrowed by those tokens (must include the target) | **≤8 events** |
| Mean question↔answer lexical overlap (anti-giveaway) | **≤0.25** |
| Max question↔answer lexical overlap | **≤0.60** |

A *distinguishing token* is a token of length ≥4 whose attempt-level document
frequency across the pinned corpus is ≤5 of 495 attempts. Requiring ≥2 of them,
and requiring that the conjunction of the question's distinguishing tokens
narrows the whole 64k-event corpus to ≤8 events *including the target*, is the
mechanical form of "the question identifies its target". A generic template
cannot satisfy it: a template's tokens are corpus-common by construction.

### Failure mode 2 — distractors adjudicated

The non-target members of the narrowed candidate set **are** the plausible
distractors, by construction of the identification check.

| Metric | Bar |
|---|---|
| Goldens with a recorded adjudication verdict | **100%** |
| Goldens with ≥1 non-target candidate explicitly adjudicated | **≥50%** |
| Goldens shipped with an unadjudicated plausible distractor | **0** |
| Goldens where a distractor was judged to also answer | **0 shipped** (hard reject) |

Each golden records, per candidate distractor, the adjudicator's `also_answers`
boolean. A single `also_answers: true` rejects the golden. A bank with any
unadjudicated distractor does not ship.

### Failure mode 3 — no generic templates

| Metric | Bar |
|---|---|
| Per-skeleton hard cap during mining | **≤2 goldens** per question skeleton |
| Max single-skeleton share of the shipped bank | **≤3%** |
| Distinct question skeletons / goldens | **≥0.80** |
| Answer spans appearing in >3 distinct attempts corpus-wide | **0** (existing `too_generic` check) |

A *skeleton* is the question with quoted strings, file paths, dotted/underscored
identifiers, CamelCase identifiers, and digits erased, then whitespace-collapsed.
The v3 bank would have scored a single-skeleton share of ~48% and would have
been rejected by this check alone.

### Agent adjudication and human spot-check

| Metric | Bar |
|---|---|
| Goldens agent-adjudicated | **100%** |
| Spot-check sample emitted for owner review | **15 goldens** (gitignored) |
| Spot-check state recorded in the lock | **required** |

The spot-check state starts at `emitted_pending_owner_review`. The bank is
usable for Phase 1b/1c retrieval work in that state; **promotion of any
published number** requires the owner to have reviewed the sample and the state
to be advanced.

### Accept rate (the dataset kill gate)

| Metric | Bar |
|---|---|
| Accept rate = shipped goldens / generation calls attempted | **≥40%** |

Below 40% the source material — not the threshold — is the problem: the miner
is spending most of its candidates on trajectories that cannot support an
identifying question. That is a **STOP** and an escalation of the dataset
question, per the Phase 1 kill gate. The bar does not move.

### Determinism and cost

| Metric | Bar |
|---|---|
| Warm-cache rerun re-emits byte-identical goldens | **required** (`--verify-lock` exits non-zero otherwise) |
| Paid API spend | **$0** — generation and adjudication run on subscription-model agent calls, cached by content hash, so reruns are free |

Candidate selection is a seeded round-robin over shape buckets sorted by a
stable candidate key. Every agent reply is cached under
`sha256(kind + system + prompt)`, so the mined bank is a pure function of the
pinned corpus, the seed, and the cache.

## Reproduction

```
python3 scripts/materialize_public_code_lane.py \
  --out-corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --out-golden docs/build-log/artifacts/track-r/adapter-goldens.jsonl \
  --out-lock  docs/build-log/artifacts/track-r/corpus-adapter.lock.json
python3 scripts/track_r_mine.py --stage mine     # iterates: emits pending agent requests
python3 scripts/track_r_mine.py --verify-lock    # determinism check
```
