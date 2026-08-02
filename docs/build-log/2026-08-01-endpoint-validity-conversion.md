# S10 — is `hit@k` a valid endpoint for this program?

**Date:** 2026-08-01 · **Branch:** `s10-conversion` (base `main` @ `a43cd574`)
**Reader and judge:** `anthropic/claude-opus-5`, `--judge-profile rag-supported-v1`,
`--prompt-version 3`, `--provider-only anthropic`
**Status:** §1–§6 written before the first paid call; results appended below the
marked line.

Every number this program has produced on the coding lane is a retrieval
metric. The grep verdict (0.9667 vs 0.5889 packed hit@10), the
SWE-ContextBench comparator table, the whole Arm-K/B1 line, the in-flight
N-sweep — all `hit@k`. Not one of them has been shown to predict whether a
reader answers the question.

There is direct evidence the proxy is broken. The Phase-3 pilot
(`docs/build-log/2026-08-01-phase3-coding-reader-qa.md`) observed the scoped
BM25 control answering correctly on 0.4667 of 30 rows while its hit@10 was
0.2556. If a system answers correctly on rows where the adjudicated gold span
was never retrieved, the gold span is not what carries the answer. That was
flagged as "a bigger finding than the primary contrast" and never resolved.

This lane resolves it, and its deliverable is **not** which arm wins. It is the
joint distribution of retrieval and answer correctness, per arm:

|  | answer_correct | answer_wrong |
|---|---|---|
| **gold retrieved** | a | b |
| **gold NOT retrieved** | **c** | d |

`c` is the finding. `b` says the bottleneck is downstream of retrieval. If
P(correct | gold retrieved) and P(correct | gold not retrieved) are close,
`hit@k` is a broken endpoint and this document says so in those words.

---

## 1. Bound, stated ahead of the number

Read every figure below inside these bounds. They are properties of the
instrument, not caveats attached after the result.

1. **The bank is one bank, and its queries are over-corrected.** Track R
   paraphrase bans identifier surfaces, while real engineers name files and
   symbols. Coverage brackets it: `paraphrase 0.1346 < human 0.175–0.287 <
   original 0.396`. The paraphrase bank sits *below* the human range, so it is
   harder than the queries it stands in for, in the specific direction of
   removing lexical anchors.
2. **The haystack is one attempt (~122 items mean).** Retrieval here is
   attempt-scoped. Nothing measured on it transfers to a repo-scale or
   multi-attempt haystack without re-measurement.
3. **n = 180 goldens, and there is no more of the bank.** Effects below the
   realized MDE are unreachable on this instrument, not absent.
4. **The two packs differ in volume by construction.** After one packer at one
   k and one budget, mean packed tokens are MemPhant 2177.5 and agentic-grep
   495.5 — a 4.4x spread with **zero** rows changed by the equalization pass on
   either arm. That is not a stage artifact (both arms were already at the
   packed stage, and the manifest records the no-op numerically); it is the
   arms' own behaviour. A reader difference between them is therefore
   confounded with pack size, and is reported as such.
5. **The agentic-grep arm carries 3 rows it never searched** (`content_filter`
   refusals at retrieval time, recorded in its own provenance `liveness`
   block). Those rows enter the reader with a near-empty pack.
6. **The text check that detects "answer present without the gold span" is a
   normalized substring match.** It over-counts short gold answers that appear
   incidentally, so it is an UPPER bound on "the fact was in the pack under a
   different span" and a LOWER bound on "the reader knew it". It is reported as
   bounds, never as a partition.

## 2. Arms — banked packs, no retrieval re-run

| arm | pack | banked hit@10 | reader prompt | role |
|---|---|---:|---|---|
| `memphant` | `run-fusion/fusion_probe-evidence.jsonl` (fused, packed top-10) | **0.5889** | evidence | treatment |
| `agenticgrep` | `run-s4/agentic-final-evidence.jsonl` (174/180 hits, $25.93) | **0.9667** | evidence | control |
| `nomemory` | minted, empty pack | — | **closed-book** | saturation check |

No retrieval is re-run. These are exactly the packs whose `hit@10` the program
has been quoting, which is what makes the joint distribution meaningful: the
retrieval column of the 2x2 is the banked metric itself, not a re-derivation of
it.

Both arms bind to the same bank — `golden_sha256
4aed8e99dbf13d942d0e1d79b637ca5ee37b3dc30707a65ea3e9ffcd22bf4326`,
`corpus_sha256 c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669`
— and `code_lane_reader_prepare.py` re-asserts pairing field by field
(`question`, `question_type`, `gold_answer`, `is_abstention`) before anything
reaches a reader.

## 3. Endpoint: `answer_correct`, not `correct`

Under `rag-supported-v1`, `correct = answer_correct AND fully_supported`, and
`fully_supported` cannot be true against an empty pack — the strict parser
rejects a true flag with no cited evidence ranks. Scoring on `correct` makes the
no-memory arm 0 by construction and turns every comparison against it into a
tautology. Confirmed again in this lane's own $0 dry run: on the `correct`
endpoint the no-memory arm scores 0.0 with the reader answering on every row.

**Primary endpoint: `answer_correct`.** `correct` is emitted as a secondary
artifact for the two arms that both have packs.

## 4. Prompt version 3

v1 flatters us. Its plain abstention line made the Phase-3 control abstain on
20–30% of rows, each scored incorrect with no judge call; because better packs
produce fewer abstentions, a high-abstention prompt *amplifies* the MemPhant
margin. The `pilot-sat` reports on this bank are v1 and are prompt-robustness
datapoints only. This lane runs `--prompt-version 3` (v1-terse phrasing with v2's
calibrated abstention, CoT-routed only for temporal-reasoning and counting
questions), matching Phase 2 and Phase 3's amended pin.

## 5. Known live-API defects, carried not rediscovered

Three defects this program has already paid for, and what this lane does about
each:

1. `require_parameters` with an unpinned provider routes `anthropic/claude-opus-5`
   to a provider that rejects structured outputs — 30/30 rows lost to HTTP 400.
   **`--provider-only anthropic` is passed on every arm**, and the dry run
   verified the emitted provider block is
   `{"only": ["anthropic"], "allow_fallbacks": false, "max_price": {...}}`.
2. `minimum` on an integer is rejected by Anthropic's structured-output
   validator; it silently failed 21/21 judge calls while the reader was
   answering correctly, which is indistinguishable from 0% accuracy. The schema
   on trunk no longer carries it; the dry run confirmed
   `judge_parse_status: strict_valid` on every row.
3. A zero-byte ledger is treated as truncated and refuses to open the campaign.
   `code_lane_reader_packet.py` does not create the journal; an absent path is
   the valid empty state.

Price pinned from the live catalogue, fetched $0 and unauthenticated on
2026-08-01: `anthropic/claude-opus-5`, context 1,000,000, prompt **$5.00/M**,
completion **$25.00/M**. Both are pinned into `provider.max_price`, so an
upstream price change fails the call rather than silently costing more.

### 5.1 The $0 stub round trip, completed before any paid call

`scripts/openrouter_stub_server.py` on loopback, 6 questions x 3 arms, full paid
code path (manifest validation → campaign ledger → reservation → request →
strict parse → judge → settlement → report → comparison):

| observed at the stub | value |
|---|---|
| calls | **36** = 18 reader + 18 judge |
| model on every call | `anthropic/claude-opus-5` |
| `response_format` | present, `strict: true`, 36/36 |
| provider block | `{"only":["anthropic"],"allow_fallbacks":false,"max_price":{"prompt":5.0,"completion":25.0}}`, 36/36 |
| `max_tokens` / `temperature` | 1024 / 0 |
| Authorization header | `Bearer stub-no-credential` — no real key left the process |
| distinct system prompts | **3** (evidence reader, closed-book reader, rag judge) |
| reader prompt size | 259 – 25,524 chars |
| reader calls with zero evidence ranks | **6** — exactly the no-memory arm |
| real dollars | **$0** |

The dry run also exercised the new 2x2 decomposition end to end.

## 6. Instrument changes made for this lane

Two edits to `scripts/code_lane_reader_compare.py`, both additive:

* `gold_span_decomposition` now emits the explicit joint 2x2
  (`a_hit_correct`, `b_hit_wrong`, `c_miss_correct`, `d_miss_wrong`), Wilson
  95% intervals on both conditional rates, a two-proportion z-test on their
  difference, and the phi correlation and raw agreement between the `hit@10`
  vector and the `answer_correct` vector. Wilson rather than Wald because the
  conditional cells are small and the rates run near 0 and 1.
* `--control-description`, because the harness block previously hardcoded
  "deterministic attempt-scoped BM25" into the evidence contract, which would
  have named a control this lane does not use.

`scripts/s10_conversion_run.sh` is a thin driver over the three existing
primitives (`code_lane_reader_prepare.py`, `code_lane_reader_packet.py`,
`code_lane_reader_compare.py`). Nothing is reimplemented.

**Preregistered reading rule.** `n_d >= 6` is the structural floor for any
McNemar contrast; below it the result is NOT A MEASUREMENT whatever `p` says.
Realized psi and MDE come from this run's own cells via
`scripts/instrument_power.py`, never inherited. If the no-memory arm matches or
beats the packed arms on a slice, the bank cannot express a memory effect and
the comparison is VOID — checked before the full spend.

---
<!-- RESULTS APPENDED BELOW THIS LINE AFTER THE PAID RUN -->

# RESULTS — n = 180, endpoint `answer_correct`

**Verdict: `hit@k` is VALID BUT CONSERVATIVE. It is not a broken endpoint.**

The hypothesis this lane was commissioned to test — that `hit@k` does not track
the outcome — is **refused by the data**. The two conditional rates are not
close; they are as far apart as this instrument can resolve.

| arm | answer accuracy | banked hit@10 | abstentions | provider refusals | errored rows |
|---|---:|---:|---:|---:|---:|
| `memphant` | **0.7722** (139/180) | 0.5889 | 10 | 0 | **0** |
| `agenticgrep` | **0.9389** (169/180) | 0.9667 | 3 | 3 | **0** |
| `nomemory` | **0.1278** (23/180) | — | 108 | 53 | 3 |

**These accuracies are LOWER BOUNDS, stated here and not in a footnote.** A
provider refusal is scored as a non-answer, so those rows are
guaranteed-incorrect for a reason that is not the pack: `agenticgrep` +3,
`nomemory` +53. Each arm's true ceiling is its figure plus its refusal count.
`memphant` has no refusals, so its 0.7722 is exact — and note that the bound
runs *against* our own arm's apparent advantage, not for it.

## 1. The joint distribution — the deliverable

**`memphant`** (fused pack, hit@10 0.5889)

|  | answer_correct | answer_wrong |
|---|---:|---:|
| **gold retrieved** | a = **106** | b = **0** |
| **gold NOT retrieved** | **c = 33** | d = 41 |

* P(correct \| gold retrieved) = **1.0000** [0.9650, 1.0000] (106/106)
* P(correct \| gold NOT retrieved) = **0.4459** [0.3382, 0.5591] (33/74)
* difference +0.5541, z = 8.721, two-sided **p = 2.76e-18**
* phi(hit@10, answer_correct) = 0.6500 · raw agreement = 0.8167

**`agenticgrep`** (agentic grep/read pack, hit@10 0.9667)

|  | answer_correct | answer_wrong |
|---|---:|---:|
| **gold retrieved** | a = **167** | b = **7** |
| **gold NOT retrieved** | **c = 2** | d = 4 |

* P(correct \| gold retrieved) = **0.9598** [0.9193, 0.9804] (167/174)
* P(correct \| gold NOT retrieved) = **0.3333** [0.0968, 0.7000] (2/6)
* difference +0.6264, z = 6.298, two-sided **p = 3.01e-10**
* phi(hit@10, answer_correct) = 0.4694 · raw agreement = 0.9500

The two rates are separated by more than half the scale on both arms, at
p < 1e-9. **`hit@k` measures something real.**

What it does *not* do is measure it without bias. `memphant`'s hit@10 is 0.5889
and its answer accuracy is **0.7722** — the retrieval metric understates the
outcome by **18.3 points**, because 33 of its 74 gold-misses are answered
correctly anyway. The gold span is *sufficient* (b = 0: when it is in the pack
the reader is never wrong) but it is **not necessary**. Every coding-lane
`hit@k` figure this program has published is therefore a **floor** on the
behaviour that matters, not an estimate of it.

The text check bounds where those 33 came from: the gold answer STRING was
absent from the pack on **at least 33 of 33**. So they are not "the fact was in
the pack under a different span" — on this bank that bucket is empty. They are
inference over the pack, or the reader's priors, and the no-memory arm sizes the
priors channel at **0.1278**.

## 2. Reconciliation with the Phase-3 anomaly — and it is undramatic

Phase 3 observed the scoped BM25 control answering correctly on 0.4667 of 30
rows while its hit@10 was 0.2556, and flagged it as "a bigger finding than the
primary contrast". **It is not a broken proxy. It is P(correct | gold not
retrieved) ≈ 0.45, doing exactly what these cells predict.**

Put the two numbers side by side: this lane measures P(correct | gold not
retrieved) = **0.4459** on `memphant` and 0.3333 on `agenticgrep`. A control at
hit@10 0.2556 whose misses are answerable at ~0.45 lands at roughly
0.26 + 0.74x0.45 ≈ 0.59 in expectation, of the same order as the 0.4667 observed
on 30 rows. The anomaly was never evidence that retrieval metrics are invalid;
it was an unmeasured constant, and it is now measured.

The open item closes, and it closes boringly. That is worth saying plainly,
because the finding was cited as the biggest unresolved item in the repo and a
quiet resolution is easy to mistake for an unfinished one.

## 3. Does the retrieval gap convert? Yes — at 44%

| quantity | value |
|---|---:|
| hit@10 gap (`agenticgrep` − `memphant`) | **+0.3778** |
| answer-accuracy gap, same 180 questions | **+0.1667** |
| **conversion ratio** | **0.441** [0.286, 0.587] |

Paired bootstrap over question ids, 20,000 draws, seed 20260801, percentile CI;
0 draws dropped as undefined. Computed by `scripts/s10_report_tables.py` from
the artifact's own per-question vectors, not by dividing two point estimates.

**A retrieval point is worth about 0.44 of an answer point here, and the CI
excludes 1.0 comfortably — 55.9% of this retrieval gap does not arrive at the
outcome.** The mechanism is not mysterious: roughly 45% of gold-misses are
answerable anyway, so closing a miss only converts when it was one of the ~55%
that was not.

**This ratio is arm-pair-specific and must not be generalized.** The two arms
differ **4.4x in mean packed tokens** (2177.5 vs 495.5), with **zero rows
changed by the equalization pass on either** — so that spread is the arms' own
behaviour, not a stage artifact. This is "what *this* gap converts to *between
these two arms*". Extrapolating it to a sweep whose arms are variants of one
retriever at similar pack sizes is not supported by anything measured here.

## 4. The primary contrast, and an asymmetry that runs our way

`memphant` vs `agenticgrep`, endpoint `answer_correct`:
b = 8, c = 38, **n_d = 46**, delta = **−0.1667**, two-sided exact McNemar
**p = 9.25e-06**, realized psi 0.2556, MDE@80% 0.1082, **achieved power 0.996**,
required n 75. Well clear of the n_d >= 6 floor. **The agentic-grep arm's
retrieval win converts into a real answer-correctness win**, at 44% of its
nominal size.

On the stricter secondary endpoint `correct` (answer correct AND fully
supported) the gap widens to delta = −0.3111 (b = 6, c = 62, p = 8.18e-13):
`memphant` 0.6278 vs `agenticgrep` 0.9389. The no-memory arm is 0.0000 there by
construction, which is precisely why it is not the primary endpoint.

**The asymmetry, which is the one place our arm looks better.** `agenticgrep`
retrieves the gold span and still answers wrong **7 times**; `memphant` does so
**0 times**. Fisher exact on 0/106 against 7/174 gives **p = 0.0471** — over the
line by a hair, on a comparison that was **not preregistered**. Treat it as
hypothesis-generating, not as a result. It shows up again as phi: 0.6500 for
`memphant` against 0.4694 for `agenticgrep`, i.e. `hit@10` predicts the outcome
*better* on our pack than on grep's.

Candidate mechanisms, none adjudicated here, and note the obvious one is ruled
out: it is **not** distractor density from a larger pack, because the arm with
the 4.4x *larger* pack is the one with zero such failures. What remains is that
a grep "hit" and a MemPhant "hit" are not the same object — grep returns
6000-character excerpts around a match, so the adjudicated span can be present
while the context that makes it answerable is clipped, whereas MemPhant packs
whole episodic units with provenance headers. If that is right, `hit@k` is not
even commensurable across retrieval mechanisms, which would matter for every
cross-arm comparison in the program. Testing it needs a span-completeness
measurement this lane did not run.

## 5. Instrument honesty

Reader errors were driven to zero on **both comparator arms** (`memphant` 0/180,
`agenticgrep` 0/180) before any figure above was read. Getting there required
three fixes made *during* the run and committed with their evidence: attributing
`empty_content` (it was `length` — reasoning tokens consuming a 1024 budget),
not retrying deterministic `content_filter` refusals four times, and retrying
schema-violating `{}` replies in the transport where the retry loop can act
rather than at the parser where the row is already lost.

Three residual errors sit on the `nomemory` arm. It is the saturation probe and
never the paired comparator for a memory claim; its 0.1278 is already a lower
bound by 53 refusals, and 3 rows cannot move a saturation verdict that clears
its nearest arm by 64 points.

Lineage caveat, stated rather than buried: the stage-equalization manifest
carries `git_dirty: true`. The lane's own harness commits landed while the
campaign ran; the packer, the prepare script and the equalized evidence files
were not among the files changed, and every arm is bound to the manifest by
sha256, which is the gate that matters. But the dirty flag is real and is not
being explained away.

**Settled spend: $27.05** across the three arms ($18.34 memphant, $6.31
agenticgrep, $2.39 nomemory), plus ~$2.40 to re-run the saturation arm and ~$7
in pilots and aborted pilots — well inside the $150 ceiling. The $0 stub round
trip caught nothing new this time, which is the outcome a governance control is
supposed to have once it is working.

## 6. What this changes

1. **`hit@k` stays.** It is a valid, conservative, directionally correct
   endpoint. No retrieval result in this program is invalidated.
2. **Every coding-lane `hit@k` figure is a floor, not an estimate.** MemPhant's
   0.5889 corresponds to 0.7722 answer accuracy on this bank. Quoting retrieval
   numbers as if they were outcome numbers understates our own system.
3. **Retrieval points are discounted ~56% at the outcome, between these two
   arms.** Any lane trading effort for retrieval points should price them at
   roughly 0.44 — and should measure its own conversion rather than borrow this
   one.
4. **b = 0 means the reader is not the bottleneck on this bank.** When the gold
   span is packed, `claude-opus-5` answers correctly every time in 106 chances.
   Reader-side work has no headroom here; the headroom is all in retrieval.
5. **Open, unresolved:** whether a "hit" is commensurable across retrieval
   mechanisms (§4). If it is not, cross-mechanism `hit@k` comparisons — which
   this program makes routinely — need a commensurability check first.
