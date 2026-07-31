# R1.5 — Rank compression — 2026-07-12

> **RETRACTION, 2026-08-01 — the R6 row below is a NON-MEASUREMENT, not a null.**
> The "NOT unlocked" verdict on the R6 unlock rule rests on a bootstrap CI whose
> floor touched exactly 0.000. The exact paired test was never run on those rows.
> It has now been run: **n=120, b=18, c=8, ψ=0.2167, δ=+0.0833, exact two-sided
> McNemar p = 0.0755**, against a true **MDE of 12.3pt**. An 8.3pt effect measured
> on a 12.3pt instrument could not have been proven at this n — "proof is not"
> was a foregone conclusion of the sample size, not a finding about the lever.
> **Stop citing this row as evidence that the docs lane has no effect.** It is
> evidence of nothing either way. Deciding it needs n = 370 (500 at the upper end
> of ψ's own interval) and costs $57.72–$121.39; see
> `docs/build-log/2026-08-01-r6-docs-decision.md` for the derivation, the
> corpus-lineage break that makes these 120 rows unpoolable with any new set, and
> the $0 pre-check that must run first.
>
> The **L2 latency retirement** in the same table has also been superseded twice
> and should not be quoted bare either: `2026-07-22-reranker-latency-spike.md`
> cut 64×512 to a measured 449 ms with MiniLM-L6-int8, and a re-measurement on
> 2026-08-01 put the same matrix at **1140–1871 ms on this host** — inside the
> 1.5 s ceiling at the median, breaching it at the tail. See
> `2026-08-01-r6-docs-decision.md` §K.

Owner-authorized order insert before R2. Pre-registration `.superpowers/sdd/r15-plan.md` (rules
frozen before runs). Base `8481e1b`; commits `baf7144`+`10ca6f7` (pool-depth decoupling + fix),
`800ac41` (server-side cross-encoder, flag-gated). Lattice unchanged; binaries from a clean
worktree at `800ac41`; Syndai comparison = the R1-committed arms. CI table:
`docs/build-log/artifacts/r15-docs/r15-verdict-cis.json`.

## Verdicts (frozen rules applied)

| Lever | Result | Verdict |
|---|---|---|
| **L1 pool-depth decoupling** (`recall_pool_depth=64`, k-invariance) | docs QA +0.000 [−0.033,+0.033] vs R1-A2; chat −0.010 ns | QA-promotion criterion NOT met. **Default kept on correctness grounds** — requested k must not choose your top-5 (D1 bug, now contract-tested by two red-first tombstones; k5≡k50 top-5). Explicit rule amendment, recorded, not silent. |
| **L2 server-side cross-encoder** (bge-reranker-base over the deep pool, flag `MEMPHANT_CROSS_RERANK`) | attribution **+0.158 [+0.067,+0.250] excl-0** — the largest single-lever QA gain of the campaign. L1X: QA .333/.300 at k=10/~11.4k chars; v2 R@5 .250 **beats Syndai's .200**. | **Accuracy-validated, latency-RETIRED**: measured `cross_rerank_ms` ≈ **12.9–13.6 s/query** (64 full-section candidates × ~200 ms/pair CPU; the 625 ms smoke used short texts). Breaches the pre-registered 1.5 s ceiling 9×. Stays flag-gated with evidence. |
| **Resource chunks** (re-adjudication) | +0.008 ns vs L1; +0.008 ns on top of rerank | Third consecutive ns. Stays flag-gated; **retirement candidate** at R2 close. |
| **R6 unlock rule** (comparable-volume flip, CI-clean) | best arm L1XC vs Syndai pooled **+0.083 [+0.000,+0.167]** — floor exactly 0.000 | **NOT unlocked.** Agonizing, but the rule is the rule. Parity-to-better at comparable volume is real; proof is not. |

## What this wave actually established

1. **Ordering, not depth, is the bottleneck.** Depth alone (L1) moved nothing at k=10 — D1's
   apparent fan-out gain lived in the k-derived packing/return path, not in candidate starvation.
   The cross-encoder converts the same pool into +15.8 pts. Fusion's static ordering is the
   deficit; a learned ranker over it is the fix.
2. **The comparable-volume scoreboard after one lever:** L1X at k=10 delivers k50-class QA
   (.333/.300 vs diag-k50's .283/.400) at 3.5× less reader input than k=50 and beats Syndai's
   retrieval precision on v2. The remaining blocker is pure rerank latency.
3. **Latency reality of CPU cross-encoders at section length:** ~200 ms/pair — input-length
   dominated. Named follow-ups (R-next, in order): (a) truncated-input rerank (rerank on the
   first ~512 tokens — likely near-free accuracy-wise since bge tokenizers truncate anyway,
   verify), (b) rerank the top-32 not 64 (halves cost; gold is mostly ≤32 post-L1), (c) smaller/
   faster reranker arm, (d) async second-pass UX. A 4–8× cut lands inside the ceiling.
4. k-invariance is now a tested contract; chat lane is regression-clean through both levers.

## Operating recommendations (unchanged priority: accuracy/UX > cost > perf/latency)

- Syndai docs-lane TODAY: `k=50, budget 8192, modernbert` (accuracy-max, no rerank) — or
  `L1X @ k=10` where a ~13 s retrieval stage is acceptable (async knowledge panels): similar
  accuracy, 3.5× cheaper reader tokens. Their product call; both documented.
- MemPhant defaults shipped this wave: `recall_pool_depth=64` (correctness). Everything else
  flag-gated: `MEMPHANT_CROSS_RERANK` (accuracy-validated/latency-retired),
  `MEMPHANT_RESOURCE_CHUNKS` (3× ns).

## Ops notes

Chat regression pair ran on a pre-provisioned scratch DB (`memphant_r15_chat`); all docs arms on
`memphant_gate_r15` scratch via the T0-era isolation; owner's concurrent working-tree WIP excluded
from every commit (explicit-path adds, verified per review). Cost this wave ≈ $25–35 reader/judge.
