# HorizonBench Stage-0 qualification

Date: 2026-08-02

## Decision

**REJECTED before adapter or model work.** HorizonBench is the strongest public
2026 preference candidate audited in this program: it preserves stable row and
user identity, links 4,245 benchmark items to 360 provenance-rich user graphs,
and evaluates preference evolution rather than static fact retrieval. The
current release still fails MemPhant's acquisition and complete-result gates.
No dependency or dataset body was installed, no adapter was written, and no
model or provider call was made. Spend: **$0**.

This rejects the current release as promotion evidence. It does not reject the
benchmark question or its architecture.

## Immutable census

Audited the official repository at commit
`4b5076b147c952499b7921f20f811dd5aca7ef0b` and the official Hugging Face
dataset at revision `50941f00f90c03a5a60219d76393869b757b835a`.

| Item | Observed state |
|---|---|
| Repository history | Three commits; no release tag required for the pinned audit |
| Repository license | A complete Apache-2.0 `LICENSE` with a 2025 copyright notice |
| Dataset declaration | Dataset-card metadata says CC-BY-4.0 |
| Dataset license artifact | `LICENSE`, `LICENSE.md`, `LICENSE.txt`, `COPYING`, and `NOTICE` all return HTTP 404 at the pinned revision |
| Benchmark split | 4,245 rows; about 2.78 GB expanded / 1.60 GB download |
| Independent identity | Stable `id` and `user_id`; 360 released user graphs |
| Mental-state split | 360 rows; about 1.47 GB expanded / 563 MB download |
| Benchmark lineage | `user_id` links rows to user profile, timeline metadata, preference records, event records, and conversations |
| Evaluation fields | Conversation, five options, correct letter, evolved/static flag, preference domain, pre-evolution distractor, and preference-evolution metadata |

The repository's Apache license covers the project repository. It does not
resolve the separately published dataset's CC-BY declaration. A card field is
useful intent metadata but is not the real license artifact required by the
acquisition gate. No lock file was created because a lock must not imply that
the dataset was approved for use.

## Gold and evaluator boundaries

The official prompt is correctly assembled from only `conversation` and the
multiple-choice options. The correct letter, pre-evolution distractor, and
preference-evolution metadata remain outside the prompt. Gold leakage was not
established.

The official runner is nevertheless fail-open:

- dataset loading floats the Hugging Face revision;
- a per-item model exception logs a warning and `continue`s;
- final accuracy aggregates the in-memory survivor list without checking the
  complete expected ID set;
- resume trusts pre-existing JSONL rows without a pinned input/body census; and
- confidence intervals resample rows, even though rows are clustered within
  360 users.

MemPhant requires one terminal outcome for every expected row and user-clustered
paired inference. Repairing these semantics would create a maintained benchmark
fork before the acquisition gate has passed.

## Power boundary

HorizonBench is materially better powered than PERMA. At 360 independent user
clusters, a worst-case single-rate 95% half-width is about 5.2 percentage
points. A paired 7-point effect can be reachable under favorable observed
discordance, while a 3-point near-SOTA margin is not assured. The exact sample
must be recomputed from a fail-closed identity-preserving pilot; 4,245 rows
cannot be treated as 4,245 independent units.

The paper reports a best frontier-model result of 52.8%, so the benchmark is
not saturated. That makes it the preferred preference instrument to reopen,
but it does not waive license or completeness requirements.

## Reopen conditions

Reconsider HorizonBench when upstream provides:

1. a real license artifact covering the Hugging Face dataset and its generated
   contents;
2. an immutable release or manifest binding repository code and dataset bytes;
3. a fail-closed expected-row ledger for fresh and resumed runs; and
4. user-clustered paired uncertainty and power calculations.

Until then, do not download the 2.1-GB release, write a HorizonBench-specific
adapter, fork the scorer, install its model stack, or make paid calls.

## Long-term product decision

The benchmark's useful signal is architectural: keep PostgreSQL and the typed
current-state/lineage model as the sole substrate, then evaluate a thin,
selective intervention policy at the agent boundary. That policy decides when
a durable preference or constraint should change behavior; it does not create
a second memory engine, graph store, or benchmark-specific mutation path. This
is the smallest path aligned with the user experience and with 2026 work on
personalized storage gating and proactive memory application.

## Sources and reproduce

- Official repository: <https://github.com/stellalisy/HorizonBench>
- Official dataset: <https://huggingface.co/datasets/stellalisy/HorizonBench>
- Paper: <https://arxiv.org/abs/2604.17283>

```bash
git clone --no-checkout https://github.com/stellalisy/HorizonBench /tmp/HorizonBench
git -C /tmp/HorizonBench checkout 4b5076b147c952499b7921f20f811dd5aca7ef0b -- LICENSE README.md evaluate.py
git -C /tmp/HorizonBench rev-list --all --count

curl -sSfL 'https://huggingface.co/api/datasets/stellalisy/HorizonBench'
curl -sSfL \
  'https://datasets-server.huggingface.co/info?dataset=stellalisy/HorizonBench'
```
