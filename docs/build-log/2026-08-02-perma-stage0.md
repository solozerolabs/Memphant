# PERMA Stage-0 qualification

Date: 2026-08-02

## Decision

**REJECTED before adapter work.** PERMA is relevant to the preference surface,
but the current release does not satisfy MemPhant's acquisition, independent-
unit, or completeness gates. No dependency was installed, no benchmark body was
copied into the repository, and no model or provider call was made. Spend: **$0**.

This is a rejection of the current public release as promotion evidence, not a
claim that the benchmark's research question is unimportant.

## Immutable census

Audited the official repository at commit
`d678640987170e8cfbe9260b311e0493b9cd2c31` and the official Hugging Face dataset
at revision `440e64e4fb8baec6f7ad10c1de135505f93e7cb1`.

| Item | Observed state |
|---|---|
| Repository history | 65 commits; no tags |
| Repository license artifact | None in the current tree and none at `LICENSE`, `LICENSE.md`, `LICENSE.txt`, `COPYING`, or `NOTICE` anywhere in Git history |
| Dataset license artifact | All five conventional paths above return HTTP 404 at the pinned dataset revision |
| License declarations | Repository README badge and dataset-card metadata say Apache-2.0; neither is a shipped license artifact |
| Packaged table | One `test` split, 1,094 rows, 100 unique `task_id` values, 857 unique questions |
| Task types | 257 type-1, 257 type-2, 580 type-3 rows |
| Raw identities | Ten user directories: 108, 109, 112, 123, 334, 354, 419, 507, 914, 1377 |
| Packaged columns | `task_id`, question, description, goal, date, scope, type, options, gold label |
| Missing packaged lineage | No user id, interaction history, preference history, or row-stable raw-file identity |

The Apache-2.0 badge/card is useful intent metadata, but it does not meet this
program's real-artifact gate. No `perma.lock.json` was created because a lock
file must not imply an acquisition was approved.

## Gold boundary

The answer-stage code formats retrieved context, question, and options into
`ANSWER_OPTIONAL_PROMPT`; it compares the returned option with `gold_label`
after the call. The audited path does **not** place `gold_label` itself in the
answer prompt. Gold leakage was therefore not established and is not a reason
for this rejection.

The flattened table is nevertheless insufficient for a MemPhant adapter: it
contains the evaluation question and answer options but not the user timeline
that establishes and evolves the preferences. Those histories live in separate
raw directories, without a packaged row-to-user lineage field.

## Evidence-contract mismatch

The official evaluator is fail-open:

- a missing search output logs a warning and skips the answer stage;
- a missing answer output logs a warning and skips evaluation;
- missing user/evaluation directories, malformed JSON, filename/content
  mismatches, and invalid metric values are skipped;
- aggregation averages only the values that survived those branches.

That can produce a survivor-only score. MemPhant requires every expected row to
end in success or a counted terminal error before a result can be decisional.
Repairing this would mean maintaining a benchmark fork rather than writing a
small adapter against an upstream complete-result contract.

The published runner also floats the dataset download instead of pinning a
revision and hard-codes GPT-4o-mini for answers plus GPT-4o for interactive
judging. These are reproducibility and cost boundaries, not reasons to mutate
the MemPhant product.

## Independent-unit and power boundary

The 1,094 rows are repeated task types and checkpoints over 100 task identifiers
and ten user timelines; they are not 1,094 independent preference histories.
The packaged table omits user identity, so a row-to-user clustered analysis
cannot be reconstructed from it alone. The official summarizer reports means,
not confidence intervals, paired discordance, cluster resampling, or a power
calculation.

For scale only, the worst-case 95% half-width for a binary rate is about 31.0
percentage points at ten independent user clusters and 9.8 points at 100
independent task clusters, before any within-cluster design effect. Actual
paired variance could be smaller, but it must be measured from an identity-
preserving pilot. The current release therefore cannot preregister a defensible
3-point near-SOTA boundary or 7-point preference-improvement gate.

## Reopen conditions

Reconsider PERMA only after upstream provides all of the following:

1. real license artifacts covering the repository, dataset, and redistributed
   source material;
2. an immutable dataset manifest with row identity and raw user/history lineage;
3. a complete expected-row ledger and fail-closed evaluator that counts every
   missing, malformed, failed, and retried row;
4. enough independent units, or released cluster identity and discordance, to
   power the preregistered effect size.

Until then, do not build a PERMA-specific adapter, fork its scorer, install its
model stack, or spend on its official routes. The next preference candidate is
HorizonBench; the former LongMemEval-V2 v5 continuation is historical
provenance, not an executable lane, after the harness retirement recorded in
`docs/build-log/2026-07-31-phase-c-deletions.md`.

## Sources and reproduce

- Official repository: <https://github.com/PolarisLiu1/PERMA>
- Official dataset: <https://huggingface.co/datasets/ustclsc/PERMA>
- Paper: <https://arxiv.org/abs/2603.23231>

```bash
git clone https://github.com/PolarisLiu1/PERMA /tmp/PERMA
git -C /tmp/PERMA fetch --all
git -C /tmp/PERMA checkout d678640987170e8cfbe9260b311e0493b9cd2c31
git -C /tmp/PERMA rev-list --all --count
git -C /tmp/PERMA log --all --format= -- \
  LICENSE LICENSE.md LICENSE.txt COPYING NOTICE

curl -sSfL 'https://huggingface.co/api/datasets/ustclsc/PERMA'
curl -sSfL \
  'https://datasets-server.huggingface.co/info?dataset=ustclsc/PERMA'
```
