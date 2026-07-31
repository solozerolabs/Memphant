# Track R **paraphrase** variant — preregistered quality bar (W0.1)

Date: 2026-07-31
Phase: substrate-and-accuracy program, **W0.1** (instrument validity, BLOCKING)
Plan: `docs/superpowers/plans/2026-07-30-substrate-and-accuracy-program.md` §1, §W0
Status: **preregistered** — committed before the paraphrase miner was run. Any
number in `benchmarks/data/track_r_paraphrase_golden.lock.json` that fails a bar
below means the bank does not ship. The bar is not revisable downward after
mining; a failed bar is a kill-gate report, not a reason to relax a threshold.

**This does not amend `docs/build-log/2026-07-30-track-r-golden-bar.md` and does
not touch `benchmarks/data/track_r_repo_memory_golden.lock.json`.** The original
bank and its lock stand exactly as mined. This is a *second, separate* instrument
built to answer one question the first one cannot.

## 1. Why this instrument exists

The original Track R bank's questions are lexically pointed at their targets. Not
by miner sloppiness — by the preregistered identification gate working as
written. "Questions must causally identify their target" was satisfied by copying
rare identifiers out of the target event into the question, and the gate then
verified the copy.

Measured on the original bank by `scripts/track_r_leakage.py`
(artifact `docs/build-log/artifacts/track-r/leakage-original.json`,
sha256 `088812ee…`), n=180, corpus `c008142e…`:

| measure | value |
|---|---:|
| question → **target** coverage, mean / median | **0.3960** / 0.3880 |
| question → non-target, **exhaustive** floor (mean over *every* non-target event of the same attempt) | **0.1008** / 0.0942 |
| question → non-target, **one seeded draw** (the §1 form) | 0.0945 / 0.0635 |
| concentration on target, vs exhaustive floor | **3.93×** |
| concentration on target, vs sampled floor | **4.19×** |
| goldens whose question narrows the corpus to exactly one event | **105 / 180** |

This reproduces the program spec's §1 figures to the digit (0.396 / 0.388,
0.094, 4.2×, 105/180), so the bar below is stated against a number this repo
recomputed, not a number it quoted.

Third-party corroboration that this is not what production queries look like:
**CLARC** (ICLR 2026, USC, 1,245 C/C++ pairs) measures BM25 at **R@10 ≈ 18.06**
on genuine natural-language→code queries. Our scoped BM25 control scored
**0.8944** on the same metric class. A ~5× outlier against a third-party
measurement is a property of the instrument.

We then fixed lexical scoring and won large on that bank. **That is the confound
this instrument exists to resolve.** The question W0.2 answers on this bank, and
cannot answer on the original one, is: *how much of the measured win survives
when the lexical give-away is removed?*

A large shrinkage is a legitimate and important result and will be reported
plainly. Nothing is tuned to preserve the win.

## 2. The metric, pinned

`scripts/track_r_leakage.py`, sha256
`1dd9435e13dc2a6cc893923dd8ef8aeed201309d4548026527988976121395f5`.

```
coverage(question, event) = |T(question) ∩ T(event)| / |T(question)|
T(s) = set(re.findall(r"[a-z0-9_]{3,}", s.lower()))
```

The `{3,}` lowercase tokenizer is the one that reproduces §1 exactly; it is
pinned rather than chosen.

Two floors are reported. The **exhaustive** floor — per golden, the mean coverage
over *every* non-target event of the bound attempt — is the primary one: it takes
no seed, so it cannot be moved by a lucky draw. The **sampled** floor (one
uniformly random non-target event, seed 7) is kept because it is the form §1
reported.

The attempt is the right comparison set because the retrieval haystack **is**
attempt-scoped: `code_lane_run_memphant.bind_attempt_context` binds one
scope/actor/agent lane per `attempt_id`, and
`code_lane_run_deterministic.scoped_documents(..., scope="attempt")` gives the
control the same haystack.

## 3. Construct: identification without identifiers

Same corpus, same three shapes, same target size. What changes is where the
identifying information lives.

| | original bank | paraphrase variant |
|---|---|---|
| identification is established by | ≥2 rare target tokens **present in the question**, narrowing the 64k-event corpus to ≤8 events | an adjudicator affirming the question **uniquely** identifies the target *within its attempt*, given only the question |
| distinguishing identifiers | embedded in the question text | **withheld from the question**, recorded only in the adjudication block |
| distractors | the non-target members of the rare-token narrowed set | the top-5 non-target events of the same attempt **as ranked by the BM25 control against the question** |

The distractor change is the substantive one. Under the original construct the
"plausible distractors" were whatever the rare-token conjunction happened to
leave behind — which was *nothing at all* for 105 of 180 goldens. Under the
paraphrase construct the distractors are, by construction, exactly the events a
retrieval system actually returns for that question. That is an adversarial set,
it always exists in an attempt-scoped haystack, and it is the set whose
`also_answers` verdict actually bears on whether the golden is answerable.

## 4. The bar (all thresholds binding)

### 4.1 Leakage — the headline acceptance criterion

| Metric | Bar | original bank, for reference |
|---|---|---:|
| **concentration = mean(target) / mean(exhaustive non-target floor)** | **≤ 1.50** | 3.93 |
| **excess over floor reduced** — `1 − (mean_target − mean_floor)_new / (mean_target − mean_floor)_orig`, both floors each bank's own | **≥ 0.75** | — |
| mean question→target coverage, absolute | **≤ 0.25** | 0.3960 |
| max question→target coverage, per golden | **≤ 0.60** | 0.6667 |

**Why a ratio and not only an absolute.** A question about an event must share
*some* vocabulary with it — that is what makes it a question about that event
rather than a different one. The floor is therefore bank-specific and moves when
the questions change: strip the rare identifiers and both the target and the
non-target coverage rise together, because what is left is common vocabulary that
occurs everywhere in an attempt. The confound is not "the question shares words
with the target", it is "the question shares words with the target **and not with
anything else**". Concentration is exactly that quantity, and driving it to 1.0
is what "approaches the non-target floor" means operationally. The absolute bar
is kept alongside it as a sanity rail, set at the original bank's p10 (0.25) so
it cannot be satisfied by a bank that merely reshuffles the same leakage.

Both floors are reported in the lock. Concentration is binding against the
**exhaustive** floor.

### 4.2 Identifier withholding — mechanical, per golden, hard reject

| Metric | Bar |
|---|---|
| Goldens whose question contains a **rare** token of the target (length ≥4, attempt-level document frequency ≤5 of 495 attempts) | **0** |
| Goldens whose question contains a target-event **file path**, **dotted identifier**, **snake_case** token, or **CamelCase** token | **0** |
| Goldens quoting the answer span, or any ≥4-token contiguous run of it, in the question | **0** |

This is the mechanical form of "the *semantics* of identification without the
*tokens*". A distinguishing token that would have gone into the question under
the original construct is instead recorded in
`identification.withheld_terms` on the golden, where the adjudication can use it
and retrieval cannot.

### 4.3 Carried forward — the three v3 rejection-receipt guards

The v3 candidate bank was rejected
(`docs/build-log/artifacts/c3-public-code-lane-v3/rejection-receipt.json`) for
three reasons. The original bank passed all three; this variant must too.

**Guard 1 — causal identification** (now semantic, since the lexical form is
precisely what is being removed):

| Metric | Bar |
|---|---|
| Goldens the adjudicator marks `target_identified` | **100%** (hard reject) |
| Goldens the adjudicator marks `uniquely_identified_within_attempt` | **100%** (hard reject) |
| Withheld distinguishing terms recorded per golden | **≥2** |

The adjudicator is shown the question, the claimed answer, the target event, and
the distractors — and is asked whether a competent engineer reading *only the
question* would know which specific moment of this run is being asked about, and
whether any other event of this run would serve equally well.

**Guard 2 — adjudicated distractors:**

| Metric | Bar |
|---|---|
| Goldens with a recorded adjudication verdict | **100%** |
| Goldens with **≥1** explicitly adjudicated non-target candidate | **100%** |
| Goldens shipped with an unadjudicated plausible distractor | **0** |
| Goldens where a distractor was judged to also answer | **0 shipped** (hard reject) |

**The 50% floor is deliberately raised to 100%, and this resolves W0.4.** The
original bar's `≥50%` floor was not a judgement about how many goldens *should*
have distractors — it was a concession to a selector that frequently returned
none, because a conjunction of rare tokens narrowing a 64k-event corpus to ≤8
events left an empty non-target set 105 times out of 180. That floor
mis-modelled the construct: it recorded, as an acceptable property of the bank,
the very narrowing that made the bank lexically trivial. Under a BM25-nearest
selector over an attempt-scoped haystack the non-target set is never empty (every
sampled attempt has ≥2 events), so **100% is the floor the construct actually
justifies** and anything less would indicate a selector bug rather than a
property of the corpus. Achieved distractor counts are reported per golden.

**Guard 3 — no generic templates:**

| Metric | Bar |
|---|---|
| Per-skeleton hard cap during mining | **≤2 goldens** per question skeleton |
| Max single-skeleton share of the shipped bank | **≤3%** |
| Distinct question skeletons / goldens | **≥0.80** |
| Answer spans appearing in >3 distinct attempts corpus-wide | **0** |

Skeletons are computed by the same `track_r_mine.skeleton` as the original bank.
This guard is *load-bearing here in a way it was not before*: removing
identifiers from questions is exactly the pressure that produces templates, so
this is the bar most likely to bind, and it will not be relaxed if it does.

**Guard 3 also keeps the original bank's answer-side gates**, unchanged: the
answer span is copied verbatim from the target event, 8–200 characters, rejected
if it occurs in more than 3 distinct attempts corpus-wide (`too_generic`), and
question↔answer lexical overlap mean **≤0.25** / max **≤0.60**.

### 4.4 Size and composition

| Metric | Bar |
|---|---|
| Shipped goldens | **150–200** (fail below 150) |
| Per-shape minimum, each of `state-churn` / `file-symbol-grounding` / `task-resumption` | **≥40** |
| Distinct source attempts | **≥50** |
| Goldens per source attempt | **≤3** |
| Goldens per repository | **≤4** |

### 4.5 Agent adjudication and human spot-check

| Metric | Bar |
|---|---|
| Goldens agent-adjudicated | **100%** |
| Spot-check sample emitted for owner review | **15 goldens** (gitignored) |
| Spot-check state recorded in the lock | **required**, starts `emitted_pending_owner_review` |

As with the original bank: the variant is usable for W0.2 in that state;
**promotion of any published number** requires the owner to have reviewed the
sample and the state to be advanced.

### 4.6 Accept rate

| Metric | Bar |
|---|---|
| Accept rate = shipped goldens / generation calls attempted | **≥0.20** |

Stated at 0.20 rather than the original bank's 0.40, deliberately and with the
reason recorded **before** mining: the paraphrase construct adds two hard gates
the original did not have — mechanical identifier withholding (§4.2) and
adjudicated *semantic* uniqueness within the attempt (§4.3 Guard 1) — while
removing none. The same numeric floor would therefore not be the same test. This
is not a relaxation of the original bar, which is untouched and still governs the
original bank.

Below 0.20 is a **STOP**: it would mean the corpus cannot support
identifier-free identification at all, which is itself a reportable finding about
the corpus and would leave ownership question (d) undecidable on Track R rather
than decided in either direction.

### 4.7 Determinism and cost

| Metric | Bar |
|---|---|
| Warm-cache rerun re-emits byte-identical goldens | **required** (`--verify-lock` exits non-zero otherwise) |
| Paid API spend | **$0** — generation and adjudication run on subscription-model agent calls, cached by `sha256(kind + system + prompt)`, so reruns are free |

Candidate selection is a seeded round-robin over the three shape buckets, over a
candidate list sorted by a stable key. The mined bank is a pure function of the
pinned corpus, the seed, and the reply cache.

## 5. Corpus — pinned, identical to the original bank's

Unchanged and re-verified before mining:

- Source `nebius/SWE-rebench-openhands-trajectories`, revision
  `35455389ab51bf5e2306bfd436ef72d0f98bf882`, license CC-BY-4.0.
- Materialized by `scripts/materialize_public_code_lane.py`, transform
  `openhands_trajectory_to_syndai_content_events_v2`, 4000-char event clip:
  495 attempts / 330 repositories / 64,055 content events.
- **corpus sha256 `c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669`**,
  verified in this worktree before the miner ran.
- Classification: public synthetic agent rollouts over real issues. **Never**
  describable as organic production traffic.

Using the identical corpus is what makes the W0.2 comparison against the original
bank's numbers a comparison of *instruments* rather than of corpora.

## 6. Custody

Bank bodies, the spot-check sample, the reply cache, and the corpus are
gitignored. The lock file is the only committed bank artifact. Every gitignored
input is additionally mirrored to `~/.memphant-private/track-r-paraphrase/`
with its sha256 recorded in §8 of this document on completion —
gitignored-and-single-copy is how this repo already lost a 64k-event corpus.

## 7. Reproduction

```
# corpus (already pinned; verifies to c008142e…)
python3 scripts/materialize_public_code_lane.py \
  --out-corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --out-golden docs/build-log/artifacts/track-r/adapter-goldens.jsonl \
  --out-lock   docs/build-log/artifacts/track-r/corpus-adapter.lock.json

# the reference figures in §1
python3 scripts/track_r_leakage.py \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --out    docs/build-log/artifacts/track-r/leakage-original.json

# mine the paraphrase variant (loop until exit 0)
python3 scripts/track_r_paraphrase_mine.py --stage mine
python3 scripts/track_r_paraphrase_mine.py --verify-lock
```

## 8. Achieved figures and custody hashes

Filled in on completion, from executed runs only.

### 8.1 The bank

Mined `2026-07-30`, 180 goldens, 60 / 60 / 60 across the three shapes, accept
rate **0.7895** (180 accepted of 228 generation calls attempted). Warm-cache
re-mine reproduces **byte-identical** (`--verify-lock`: recorded `4aed8e99dbf1`,
remined `4aed8e99dbf1`, 180/180, OK). **$0 paid API spend**; 839 cached
subscription-agent replies.

**`bar_passed: false`.** Twenty of the twenty-one mechanical checks pass. The one
failure is the headline leakage criterion, and §8.2 is the interpretation.

| check | result |
|---|---|
| `leak_concentration_le_1_50` | **FAIL** — achieved **2.0180** |
| every other check (20) | PASS |

Notable passes: zero identifier leaks; **180/180** goldens carry ≥1 adjudicated
distractor (900 distractor verdicts total, zero `also_answers` shipped); **180
distinct skeletons for 180 goldens** (max single-skeleton share 0.0056); mean
question↔answer overlap 0.0162; 155 distinct attempts, 134 distinct repositories;
mean 19.45 withheld terms per golden.

Rejections, in order of size: `adjudication_not_unique_in_attempt` 25,
`distractor_also_answers` 13, `too_generic_span` 8, `per_repo_cap` 3,
`adjudication_target_not_identified` 1, `insufficient_withheld_terms` 1. Zero
`identifier_leaked`, zero `answer_run_leaked`, zero `generic_skeleton`, zero
parse failures. **The semantic-uniqueness gate did the work the withheld
identifier tokens used to do** — it is the single largest rejection reason, which
is what a working replacement gate should look like.

### 8.2 Leakage achieved, against the reference and against the bar

Both banks measured by the same pinned `scripts/track_r_leakage.py` on the same
pinned corpus. The non-target floor is **same-attempt** — i.e. hard negatives
from the same domain, not random corpus events.

| measure | original bank | **paraphrase bank** | bar |
|---|---:|---:|---|
| question→target coverage, mean | 0.3960 | **0.1346** | ≤0.25 **PASS** |
| question→target coverage, median | 0.3880 | 0.1286 | — |
| question→target coverage, max | 0.6667 | 0.5000 | ≤0.60 **PASS** |
| non-target exhaustive floor, mean | 0.1008 | 0.0667 | — |
| concentration vs exhaustive floor | 3.9286 | **2.0180** | ≤1.50 **FAIL** |
| concentration vs sampled floor | 4.1905 | 2.0518 | — |
| excess over floor, reduced | — | **77.0%** | ≥0.75 **PASS** |

By shape (target mean / floor mean / concentration): `state-churn` 0.0983 /
0.0637 / 1.54, `file-symbol-grounding` 0.1379 / 0.0669 / 2.06, `task-resumption`
0.1675 / 0.0695 / 2.41.

**The concentration criterion is recorded as failed and the bar is not moved.**
`bar_passed: false` stands as the mechanical fact against the preregistered
number. What follows is interpretation placed beside that fact, not a revision of
it.

### 8.3 Why 1.50 was the wrong number — and where 2.018 actually sits

Two independent lines of evidence, one produced here and one supplied by the
owner, say the same thing: **≤1.50 was specified below the floor the construct
can reach.**

**(a) Measured here — the floor probe.** `scripts/track_r_floor_probe.py`
(artifact `docs/build-log/artifacts/track-r-floor/floor-probe.json`) asked the
agent, on a seeded shape-stratified sample of 36 targets from this bank's own
candidate stream, for the *most aggressively abstracted* question it could write
that a competent reader could still answer and still tie to one event — then put
those questions through the same uniqueness adjudication and the same
BM25-nearest distractors the bank uses.

| | n | target mean | floor mean | concentration |
|---|---:|---:|---:|---:|
| max abstraction, **unconstrained** | 36 | 0.1470 | 0.0721 | 2.038 |
| max abstraction, **answerable + unique** | 27 | 0.1310 | 0.0732 | **1.790** |
| same 36 targets, the bank's normal questions | 36 | 0.1398 | 0.0640 | 2.186 |

Uniqueness survival under maximum abstraction: **75%** (27/36). So pushing
abstraction as hard as the generator can and keeping only what survives
identification lands at **1.790**, not 1.50. The bank's 2.018 is roughly 13%
above a *measured* floor, not 35% above a real one.

The adjudicator's calibration note is the mechanism, and it is worth keeping:
abstraction itself was rarely what broke identification. What broke it was (i)
purely indexical anchoring with no content ("that edit", "here") and (ii)
choosing a span that is duplicated across events or is harness boilerplate. **The
ceiling on abstraction is set by span choice more than by word-borrowing.**

**(b) Supplied by the owner — human-authored calibration.** A sweep of the same
token-coverage statistic against four independent human-authored coding query
sets, using same-domain hard negatives:

| instrument | q→target | q→hard-neg | ratio |
|---|---:|---:|---:|
| our original mined bank | 0.396 | 0.094 | 4.21× |
| AMA-Bench software QA (human annotators) | 0.287 | 0.148 | 1.94× |
| SWE-rebench issues (GitHub authors) | 0.269 | 0.143 | 1.88× |
| SWE-PRBench review comments (human reviewers) | 0.197 | 0.112 | 1.76× |
| SWE-bench-Live issues (GitHub authors) | 0.175 | 0.086 | 2.03× |

**Provenance, binding:** these four rows were supplied by the owner and are **not
reproduced by this repo**. They are cited as calibration, never as a MemPhant
measurement, and nothing here should be re-reported as our own number.

Humans cluster at **1.76–2.03×**. This bank's **2.0180 (exhaustive) / 2.0518
(sampled)** sits at the top edge of, and effectively inside, that band. A ≤1.50
bar was asking the bank to be *less* lexically pointed than real human queries
are, which no answerable question set can be.

**Metric-robustness caveat, and it matters for which number leads.** The ratio is
sensitive to the negative set: against random-corpus negatives rather than
same-domain ones, the same human sets score ~3.70×. Our floor is same-attempt —
same-domain hard negatives — so 2.0180 is directly comparable to the 1.76–2.03
band and not to the 3.70. But because the ratio moves that much with negative
selection, **absolute question→target coverage is the more robust headline**, and
the ratio belongs beside it with its negative-selection stated. On the absolute
metric the original bank's 0.396 against a 0.175–0.287 human range is the durable
evidence of over-copying.

### 8.4 The bank probably overshot — stated plainly

On the absolute metric this bank reads **0.1346**, and the human range is
**0.175–0.287**. It is **below** the human floor by about 23% of the low end.

So the two banks bracket reality rather than either one hitting it:

```
paraphrase 0.1346  <  [ human 0.175 .. 0.287 ]  <  original 0.396
```

**My assessment: yes, it overshot, and the mechanism is identifiable.** §4.2 bans
*every* identifier surface — file paths, dotted names, snake_case, CamelCase, and
any corpus-rare token — from the question. Real engineers do not query that way;
they routinely name the file or the function they are asking about. The
withholding gate was designed to eliminate leakage, not to reproduce the human
query distribution, and it succeeded at the thing it was pointed at.

The consequence is directional and should be carried into every number measured
on this bank: **this instrument is harder than production, so results on it
understate real performance, and any survival ratio computed from it is a lower
bound rather than a point estimate.** It is an adversarial floor, the original
bank is an optimistic ceiling, and the truth is between them.

### 8.5 Custody

Bank bodies, spot-check, reply caches and corpus are gitignored; the lock and the
derived leakage/floor artifacts are committed. Everything gitignored is mirrored
to `~/.memphant-private/track-r-paraphrase/`.

| artifact | sha256 |
|---|---|
| `benchmarks/data/track_r_paraphrase_golden.jsonl` (180 rows) | `4aed8e99dbf13d942d0e1d79b637ca5ee37b3dc30707a65ea3e9ffcd22bf4326` |
| `benchmarks/data/track_r_paraphrase_spotcheck.jsonl` (15) | `5d71212efab98b54834b76790374b84c061a35d3991ec3610fd1bf0822440b33` |
| `benchmarks/data/track_r_paraphrase_golden.lock.json` (committed) | `02750ea17582fe224d1043da208b97164c46f026c882d9450d89fec0ba66ab3b` |
| `scripts/track_r_paraphrase_mine.py` | `5db8b4b7ed0b178fa2f1c365bd24cd2f0f283d595194d8cdf584e5524d624425` |
| `scripts/track_r_floor_probe.py` | `0e4ac183735a052fc642b3ccb69b1c11e2a260b3189c8fdbaadb6ae3cf13abc3` |
| `scripts/track_r_leakage.py` | `1dd9435e13dc2a6cc893923dd8ef8aeed201309d4548026527988976121395f5` |
| bank reply cache, 839 files (rolled hash of `sha256(sorted file hashes)`) | `491f6182c319069afe681cca73a76fe9d61a12347fd346e6a4e5cc8962f88034` |
| floor-probe reply cache, 72 files (same rolled form) | `aa8cf03d3ea56332a7ff795ecd31658275c57a22ffa1c688e51e1b8bbc4e8ee0` |
| authored generator/adjudicator brief (`fulfil-brief.md`) | `16d253a595dc467704d65bfc86bc32d233f7d82a7ec417b4a7be940decaf7124` |
| pinned corpus (unchanged) | `c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669` |

Mirror layout: `~/.memphant-private/track-r-paraphrase/{track_r_paraphrase_golden.jsonl,
track_r_paraphrase_spotcheck.jsonl, track_r_paraphrase_golden.lock.json,
fulfil-brief.md, agent-cache/, floor/{agent-cache/,floor-probe.json}}`.

### 8.6 Spot-check state

`emitted_pending_owner_review`, 15 goldens, gitignored and mirrored. Unchanged
from the original bank's rule: **no number measured on this bank is publishable**
until the owner has reviewed the sample and the state is advanced.
