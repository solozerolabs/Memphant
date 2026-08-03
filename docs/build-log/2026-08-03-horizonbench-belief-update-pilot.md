# HorizonBench belief-update pilot

Date: 2026-08-03

## Outcome

The preregistered ten-user pilot completed without missing rows, retries,
provider drift, unpriced attempts, or unsettled liability. It passes its process
gate to design a powered confirmation; it does not promote a default or support
a SOTA claim.

| Arm | Overall | Evolved | Static | Evolved old-state distractors |
|---|---:|---:|---:|---:|
| Full context | 5/10 (50.0%) | 3/6 (50.0%) | 2/4 (50.0%) | 1 |
| MemPhant Fast | 5/10 (50.0%) | 4/6 (66.7%) | 1/4 (25.0%) | 1 |
| Selective Deep | 5/10 (50.0%) | 4/6 (66.7%) | 1/4 (25.0%) | 1 |

Selective versus full context had one gain, one loss, eight ties, delta 0.0,
and a user-cluster bootstrap 95% interval of [-0.30, 0.30]. The exact McNemar
discordance count is only two, below the repository's decisional floor. All ten
Fast answers were non-abstaining, so the preregistered selective policy reused
Fast ten times and invoked Deep zero times. Selective therefore measures Fast
in this pilot; it provides no Deep efficacy evidence.

## UX, latency, and cost

The fixed reader was `anthropic/claude-opus-4.5`, served exactly as
`anthropic/claude-4.5-opus-20251124` by Anthropic. Twenty calls consumed
1,514,896 prompt tokens and 2,122 completion tokens for $7.62753.

| Prompt path | Prompt tokens | Reader cost | Reader p50 | Reader p95 |
|---|---:|---:|---:|---:|
| Full context | 1,379,099 | $6.92527 | 25.57 s | 41.59 s |
| Fast evidence | 135,797 | $0.70226 | 6.46 s | 7.17 s |

Fast preserved overall accuracy on this sample while reducing reader prompt
tokens by 90.2%, reader cost by 89.9%, and reader p50 by 74.7%. Local Fast
recall added roughly 0.53 s median in the sealed construction run. This is the
strongest current user-experience signal: keep one Postgres substrate and make
Fast the product path. Do not send six months of raw history to the reader.

## Dataset contamination and claim boundary

The pinned dataset revision was published in April 2026, while the served
reader identifies as the 2025-11-24 Opus snapshot. Direct training on this
released benchmark is therefore chronologically implausible for this reader.
Gold stayed outside retain, recall, Deep, and reader prompts, and the mental
state graphs were never acquired. The remaining limitations are different:
HorizonBench is synthetic, the ten-row split is curated, generators may leave
family-specific stylistic signals, and this pilot has only ten independent
users. It is valid evidence for the narrow evolving-preference MCQ axis, not
for general memory, storage, code lookup, or overall SOTA.

The sample score of 50.0% is numerically 2.8 points below the paper's 52.8%
full-benchmark best, and the evolved 66.7% sample score is above the reported
51.3% evolved reference. Neither comparison is inferentially valid at n=10.
The honest state is **positive evolved-preference and UX/cost signal; near-SOTA
claim still false**.

## Decision

Keep PostgreSQL, the existing sentence-unit embedder, and Fast unchanged. Do
not add a graph/vector store, tune on the released sample, or auto-enable Deep.
The reader's self-reported insufficiency is not a useful router here: it
escalated 0/10 despite five Fast errors. The next experiment must be a new,
user-clustered held-out tranche and must evaluate a calibrated routing signal
without reading scoring gold. The separately costed plan is
`docs/superpowers/plans/2026-08-03-horizonbench-powered-confirmation.md`.

## Evidence

- `docs/build-log/artifacts/horizonbench-pilot/result.json`
- `docs/build-log/artifacts/horizonbench-pilot/paid-census.json`
- `docs/build-log/artifacts/horizonbench-pilot/reader-closure.json`
- `docs/build-log/artifacts/horizonbench-pilot/fast-gate.json`
- HorizonBench paper: <https://arxiv.org/abs/2604.17283>
- Pinned dataset: <https://huggingface.co/datasets/stellalisy/HorizonBench/tree/50941f00f90c03a5a60219d76393869b757b835a>
