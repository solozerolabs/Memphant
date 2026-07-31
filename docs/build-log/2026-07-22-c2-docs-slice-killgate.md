# 2026-07-22 — C2 docs-slice pre-check: the free kill-gate fires DROP

> **AMENDED 2026-08-01 — kill-gate (b) has now been run. Two corrections to this
> document, neither of which disturbs the DROP.**
>
> 1. **The "~450 ms" premise below is retracted.** Re-measured on 2026-08-01:
>    median ~1460 ms at 64 × 512 body granularity, 2 of 4 samples over the
>    1500 ms ceiling. See the banner on
>    `docs/build-log/2026-07-22-reranker-latency-spike.md`. The latency story was
>    not "killed"; it was reduced from 9× over to roughly *at* the ceiling.
> 2. **`docs/build-log/artifacts/p1-c2-killgate/score_killgate_b.py` is
>    superseded and disarmed.** It silently substitutes a hardcoded
>    `SYNDAI_HIT10=0.200` from a *different* corpus pin whenever the live
>    incumbent arm is absent, then reports a difference of two aggregates. It
>    never ran to completion and produced **no banked number** — no `verdict.json`
>    exists here and none was ever committed — so nothing downstream needs
>    retracting. Use `scripts/score_killgate_b.py`, which refuses to mix pins and
>    reports a paired exact McNemar.
>
> Results: `docs/build-log/artifacts/p1-c2-killgate/killgate-b/verdict-b.json`;
> discussion in `docs/build-log/2026-08-01-r6-docs-decision.md` §K.

## Verdict

**DROP C2 from the roadmap.** The free pre-check registered in the tri-domain
plan (§5 C2 row, §8 kill-switches) fired on its first and most decisive leg:
of the 26 QA flips the retired r15 cross-encoder rerank won, only **3 (11.5%)
have their gold section inside the top-16 fused candidates** — far below the
60% bar. The r15 win (+0.158 QA, the campaign's largest single lever) lived in
the 16–64 candidate band that rank-compression to top-16 structurally discards.
No amount of reranking a 16-candidate head recovers a gold that sits at rank
17–64. C2's whole premise — "ship the rerank win cheaply, rank-compressed, under
the 1.5 s ceiling" — is therefore dead on the retrieval axis before any reader
spend.

Per §8 the rule is OR (either leg failing drops C2), so this alone is decisive.
Kill-gate (b) is recorded below as corroboration.

## What was un-runnable, and the free re-pin that fixed it

The docs gate could not run at all: Syndai HEAD had drifted (109 pinned → 114
eligible docs files), so `gate_common.verify_corpus_contract` hard-failed
"corpus file set mismatch" before ingest. Re-pinned against Syndai docs @
`96a26f1f` (content stable across the intervening HEAD moves): **114 files /
4920 sections / 3299 mining candidates**, new `section_revision`. Both disjoint
golden sets re-mined (v1 + v2, 60 each = 48 single + 12 multi-hop, zero section
overlap), **144/144 answer spans verbatim-verified against the live corpus**.

Two drifts fixed along the way, both pre-existing false-confidence traps:
- `run_reader.response_contract()` had become fail-closed on unknown kinds, so
  the miner's `generate_single`/`generate_multi` calls errored — the re-mine was
  blocked until those schemas were registered.
- The verbatim-span contract test resolved the Syndai corpus only as a checkout
  *sibling*, so in a git worktree the strongest pin skipped silently (exactly
  the trap the 2026-07-21 tests-audit flagged). Now resolves `$HOME` /
  `MEMPHANT_SYNDAI_ROOT` too → **28 passed / 0 skipped** with the corpus present.

`pytest tests/test_syndai_gate_contract.py` → 28 passed. Commits: `137f0571`
(re-pin), `332cebd6` (strict-contract runner rebuild — the runner still spoke
the pre-C0 tenant_id contract and 400'd against the current server).

## Kill-gate (a): flip reproducibility in top-16 — FAIL (11.5% << 60%)

Method: extract the 26 QA flips (L1 no-rerank wrong → L1X rerank right) from the
retired r15 reader artifacts (`docs/build-log/artifacts/r15-docs/{L1,L1X}/{v1,v2}/reader.json`;
net +19/120 = the recorded +0.158). Reproduce the fused no-rerank retrieval on
the **old pinned corpus** (git-archive of Syndai docs @ `fb650da`, verified
against the archived manifest) at k=16, modernbert, balanced mode. A flip is
"reproducible in top-16" iff `gate_common.provenance_hit` holds within the
returned 16 bodies (multi-hop needs both spans — identical grading to the gate).

Reproduction faithfulness check: the k=16 no-rerank modernbert arm scores
**R@10 = 0.100 (v1) / 0.150 (v2)**, mean 0.125 — matching the retired r15 L1
(0.117 v1) within run variance, so the retrieval base is faithfully reproduced
and the top-16 membership count is trustworthy.

Result: **3 / 26 flips (11.5%) reproducible in top-16.** Threshold 60%. **FAIL.**
23 of 26 flip golds sit at rank 17–64 (or out of pool), unreachable by a
top-16-head rerank. Artifact: `docs/build-log/artifacts/p1-c2-killgate/verdict-a.json`.

## Kill-gate (b): does chunked MiniLM rerank close ≥half the deficit?

Context update honored: the plan's "docs lever is latency-dead (13 s/query)"
premise was obsolete — the 2026-07-22 reranker spike proved ms-marco-MiniLM-L6
int8 reranks 64 candidates in ~450 ms, and the load-bearing finding was that
local BERT rerankers hit a hard 512-token wall and must rerank **chunks +
max-pool**, not whole bodies. So (b) was run with the strongest possible arm:
`MEMPHANT_RERANKER=byo` MiniLM-int8, `--rerank-granularity chunk`,
`--resource-chunks` (12.2% of the re-pinned corpus's sections exceed the
~2000-char 512-token wall, so chunk granularity is the fair test).

Result: **not run to completion — deliberately abandoned as corroborating-only.**
Kill-gate (a) already dropped C2 (§8 rule is OR), so (b) could only corroborate,
never overturn. The leg was set up (MemPhant base + byo-chunk-rerank arms on the
re-pinned corpus, scored pooled over v1+v2 by
`docs/build-log/artifacts/p1-c2-killgate/score_killgate_b.py` with the closure
metric `(rerank − base) / (syndai − base)` on hit@10, bar 0.5, incumbent
reference = the committed 0.200 hit@10) but the ~50-min-per-arm modernbert drains
kept dying on machine-sleep across session restarts, and re-running them for a
number that cannot move the verdict was not worth the spend. The scorer and
launchers are archived for anyone who wants to close it later.

The (a) finding already establishes why a *rank-compressed* rerank fails: 23/26
winning-flip golds are in the 16–64 band (in the r15 64-pool — that is how the
full-pool rerank originally won them — but outside the top-16 head that
rank-compression reranks). (b) would have tested the complementary question —
whether reranking the *full* narrowed pool (byo MiniLM-int8, chunk granularity,
candidate_limit 64) closes ≥half the retrieval deficit — but the DROP rests on
(a): the *ceiling* C2 was scoped to (top-16 rank-compression, the only config
under the 1.5 s bar with the retired 13 s model) cannot reach the win.

(A live Syndai incumbent re-run for (b) would also have been blocked by a Syndai
dev-DB migration drift — `knowledge_source_versions.content_sha256` absent,
alembic head newer than the column-adding migration, a Syndai-checkout
maintenance state, and per AGENTS.md the Syndai repo is used strictly as-is — so
the scorer was wired to fall back to the committed 2026-07-11 gate figure, Syndai
0.200 hit@10 on the near-identical docs corpus, which the plan's own 0.050→0.217
deficit is built on.)

## Also landed (free, cutover-safety net, valuable regardless of the verdict)

The four spec-28 coding-continuity fixture families, previously prose at
`28-syndai-code-contract.md:117-120`, are now executable `syndai-trace-compare`
fixtures: `syndai_arch_decision_honored_001`,
`syndai_compaction_rehydrate_001`, `syndai_cross_agent_transfer_001`,
`syndai_task_plus_semantic_composite_001`. The lane's fixture type became an
untagged enum (file-memory surface + a new coding_continuity surface that wraps
a golden case). `cargo test -p memphant-eval --test syndai_trace_compare` →
2 passed. Commits `e090734c` (fixtures + enum, via a sibling session's shared-
worktree add) + `9fd23ec2` (CLI fail-printer surfaces the new mismatches).

## Why this is the right call (not a defeatable near-miss)

The deficit is structural, not a tuning gap. MemPhant loses docs retrieval
0.05→0.20-class because the golden set is paraphrase-heavy (median question↔span
lexical overlap 0.0 by construction) — a near-pure semantic-matching benchmark
where Syndai's openai-3-small@1536 + HNSW/BM25/RRF stack simply retrieves
better, and the one lever that closed it (full-pool cross-rerank) is the one
rank-compression cannot afford. The honest base rate is "won't win this
quarter." Dropping now frees the Week-2 spine (B1/B2/B3) rather than gating a
loss to Week 3+.
