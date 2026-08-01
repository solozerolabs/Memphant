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
