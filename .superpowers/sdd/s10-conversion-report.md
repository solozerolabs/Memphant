# S10 — endpoint validity: does `hit@k` predict answer correctness?

**Branch:** `s10-conversion` (base `main` @ `a43cd574`) · **Date:** 2026-08-01
**Build log:** `docs/build-log/2026-08-01-endpoint-validity-conversion.md`
**Status:** IN PROGRESS — results appended when the n=180 campaign lands.

## Why this lane outranks more retrieval measurement

Every result this program has produced on the coding lane is a retrieval metric:
the grep verdict (0.9667 vs 0.5889), the SWE-ContextBench comparator table, the
Arm-K/B1 line, the in-flight N-sweep. All `hit@k`, and none of it shown to
predict whether a reader answers the question. The Phase-3 pilot found the BM25
control answering correctly at 0.4667 while its hit@10 was 0.2556 — evidence
that the adjudicated gold span is not what carries the answer — and that was
never resolved.

If `hit@k` does not track the outcome, today's grep verdict and the sweep are
optimising a proxy that may not move what users experience.

## Method

Reader-QA over **already-banked packs**: no retrieval is re-run, so the retrieval
column of the 2x2 is the banked metric itself rather than a re-derivation of it.
Three arms, one packer, one k, one budget, one reader, one judge, one prompt,
same 180 goldens:

* `memphant` — `run-fusion/fusion_probe-evidence.jsonl`, banked hit@10 0.5889
* `agenticgrep` — `run-s4/agentic-final-evidence.jsonl`, banked hit@10 0.9667
* `nomemory` — minted empty pack, closed-book prompt, saturation check

Reader and judge `anthropic/claude-opus-5`, `--judge-profile rag-supported-v1`,
`--prompt-version 3`, `--provider-only anthropic`, price pinned at $5/$25 per
million from the live catalogue. Primary endpoint `answer_correct`, not
`correct` — `correct` is `answer_correct AND fully_supported` and
`fully_supported` cannot be true against an empty pack, which would score the
no-memory arm 0 by construction and make every comparison against it a tautology.

Harness reused, not reimplemented: `code_lane_reader_prepare.py`,
`code_lane_reader_packet.py`, `code_lane_reader_compare.py`, driven by a thin
`s10_conversion_run.sh`.

## Results

_Appended after the campaign completes._

## Verdict

_Appended after the campaign completes._
