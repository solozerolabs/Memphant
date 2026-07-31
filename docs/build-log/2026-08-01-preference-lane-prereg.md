# Preference lane — first measurement, preregistration

**Date:** 2026-08-01 · **Worktree:** `Memphant-af-w8-memcode` · **Branch:** `af-w8-memcode`
**Status at writing:** NO arm has been executed. This document is committed before
any measurement; every number below is a design parameter or a count reproduced
from the pinned corpus, never a result.

## 0. Why this run exists

The preference / user-learning lane has **never been scored**. Track U's 51
goldens have sat unmeasured, and the instrument we intended to use for it
(ClawArena) was rejected this week because its corrections are model-generated
(`benchmarks/manifests/clawarena.lock.json`).

`CohereLabsCommunity/memorycode` was adopted and verified instead
(`benchmarks/manifests/memorycode.lock.json`, Apache-2.0 read off a real LICENSE
file, revision `32d888b11c73c67be91414e571dfe98c5c20feac`). Its graders are
**regexes**, so a full run needs no reader and no judge and costs **$0**.

The adoption smoke, on 26 probes, found the **superseded** session outranking the
current one on **15**. That is the staleness failure mode — "an invalidated
memory must NOT be applied" — which this program has repeatedly called the
second-worst pain after forgetting and has never measured. This run measures it
at the instrument's full power.

## 1. Corpus and probe bank (reproduced, not asserted)

Pinned source, verified by sha256 before any database is minted:

- file: `~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet`
- sha256: `1edb12380ea3410c888fffa795f6ddd3251e4e634b84a7142c8386e7c2869733`
- revision: `32d888b11c73c67be91414e571dfe98c5c20feac`

Recounted from the shipped parquet with the adopted loader's rule, 2026-08-01:

| quantity | value |
|---|---|
| instances (rows) | 360 |
| instances contributing >= 1 probe | **257** |
| ingest units (sessions in those instances) | **8147** |
| probes (supersession groups) | **1063** |
| current-vs-stale pairs covered | **3616** |

Artifact: `docs/build-log/artifacts/2026-08-01-preference-lane/probe-bank-count.json`.

**Schema trap, held explicitly.** `session_regex` / `session_eval_query` are
**not** positionally parallel to `type` / `topic` — they align in only 811 of
8400 sessions, because they are the *cumulative active-rule set* at that
session, not a per-event field. This run pairs rules to events **only** through
`type` / `topic`, which do align 8400/8400. No code path in this run indexes
`session_regex` positionally against `type`.

**Gold rule** (unchanged from the adopted adapter; uses only shipped fields, no
model call): supersession groups are formed by stripping quoted literals from
`topic` (`always start function names with 'a_'` and `... with 'y_'` collapse to
one group). Gold = the session that most recently states the convention.
Distractors = every earlier session stating the superseded form.

## 2. Arms

Exactly two, both $0, both scored on the identical 1063 probes with the identical
query strings and the identical `k`.

- **Arm A — MemPhant.** Recall as it stands. Each session ingested as one
  episode via `POST /v1/episodes` (`source_kind=user`), one bound context per
  instance, worker drained and **verified queue-empty against the database**
  (the fixed drain path, `06b0a44b`), then `POST /v1/recall`.
  `mode=fast`, `limit=10`, `budget_tokens=8192`, local fastembed default embed
  model. No reader, no judge, no provider.
- **Arm B — deterministic lexical control.** Okapi BM25 (k1=1.2, b=0.75) over the
  raw session texts, reusing the ranking function the repo already ships at
  `scripts/code_lane_run_deterministic.py:bm25_search`. Haystack is scoped to the
  instance, mirroring MemPhant's per-instance bound context exactly as the code
  lane scopes BM25 to the attempt. No embedding, no memory state, no database.

Scoring identity for both arms is the **unit id** (Arm A resolves it through the
`citation_episode_id` that `POST /v1/episodes` returned; Arm B ranks unit ids
directly). Never substring matching — recall returns citation windows that can
begin past a body's first line.

## 3. Endpoints

Per probe, let `gold_rank` be the best rank of the current session and
`stale_rank` the best rank of any superseded session, both within top-`k`,
`None` if absent. Three mutually exclusive, exhaustive buckets:

| bucket | definition |
|---|---|
| **Appropriate Application** | `gold_rank` present and (`stale_rank` absent or `gold_rank < stale_rank`) |
| **Misapplication** | `stale_rank` present and (`gold_rank` absent or `stale_rank < gold_rank`) |
| **Neither returned** | both absent |

- **Primary endpoint: latest-state-wins (LSW) rate** = the Appropriate
  Application rate. Reported for both arms.
- **Appropriate Application Rate (AAR)** = LSW.
- **Misapplication Rate (MR)** = the Misapplication bucket rate.

Both directions are reported by name, and the Neither-returned rate is reported
alongside, so a **suppression win cannot masquerade as an application win**: an
arm that returns nothing scores 0 on both AAR and MR, and that is visible.

Secondary, descriptive only, no test: `hit@1` and `hit@k` on the current
session; mean `gold_rank`; degraded-response count.

## 4. Hypotheses

- **H1 (primary, one endpoint, one comparison).** MemPhant's LSW rate exceeds
  the lexical control's. Null: `ΔLSW = LSW_A − LSW_B = 0`.
- **H2 (descriptive, no inferential test).** The adoption smoke's
  MR = 15/26 = 0.577 does not survive at scale, i.e. MR_A at n=1063 differs from
  the smoke estimate. Reported as an estimate with a cluster CI, not tested.

No other hypothesis will be tested. Any additional comparison that appears in
the report is labelled exploratory.

## 5. Analysis, fixed in advance

Probes are **nested within instances** (1063 probes / 257 instances, mean 4.1
per instance). They will **not** be treated as 1063 independent observations.

1. **Primary inference — cluster bootstrap over instances.** 10,000 resamples of
   the 257 instances with replacement, seed `20260801`, percentile 95% CI on
   `ΔLSW`. This CI is the primary inferential statement.
2. **Paired exact test — exact McNemar** (two-sided binomial, p=0.5) on the
   discordant probes for the LSW indicator between arms A and B. Reported with
   the explicit caveat that it assumes probe-level independence and is therefore
   **anti-conservative** here; it is the secondary reference, not the verdict.
3. **Cluster permutation p-value.** Arm labels flipped at the **instance** level,
   10,000 permutations, seed `20260801`, two-sided, on `ΔLSW`. Reported next to
   the McNemar p so the inflation from ignoring clusters is visible as a number.
4. MR and AAR each get a cluster-bootstrap 95% CI by the same procedure.

**Power (from the adoption pass, unchanged).** Flat paired MDE ~3.1pp at
alpha 0.05 / power 0.80 assuming ~30% discordance; cluster-adjusted ~6pp. This
is the first adequately powered lane the program has.

## 6. Decision rule

- **Positive** iff the cluster-bootstrap 95% CI on `ΔLSW` excludes 0 **in
  MemPhant's favour**.
- **Negative** if it includes 0, or excludes 0 in the lexical control's favour.
  A negative is reported plainly, in the same words as a positive would be:
  *if MemPhant cannot beat a lexical baseline at telling a live rule from a dead
  one, that is the finding.*
- No bar is set on the absolute LSW level, because the lane has no prior number
  to set one against. This run establishes the number.

## 7. Stopping and deviation rules

- **One full run per arm.** All 1063 probes. No interim analysis, no early stop,
  no re-run on an unfavourable result. If a run aborts on an infrastructure
  error, the aborted artifact is kept and the cause is named in the report.
- No arm, endpoint, filter, or exclusion may be added after any result is seen.
  Anything added is labelled **post hoc** and excluded from H1.
- Any deviation from this document is listed in a "Deviations" section of the
  result report with its reason.

## 8. Cost control

**$0.** Regex/rule-derived gold only; no reader, no judge, no paid model call on
any path. Arm A uses local fastembed and local Postgres; Arm B touches no
network at all. `gate_runtime.check_embed_model_key` gates any hosted embedder,
and no hosted embedder is requested. If any path would make a paid call it is
gated off and said so in the report.

## 9. Runtime hygiene

- Scratch DBs only, minted by `scripts/with_scratch_db.sh`, dropped on exit.
- Fresh port, release binaries built in this worktree.
- Worker drained via the **fixed** `gate_runtime.drain_worker` (`06b0a44b`) which
  re-invokes until the **database** reports `job_state` empty and fails closed on
  a no-progress invocation. The worker's self-report is never trusted.
- Corpus pinned by revision, mirrored locally, sha256 verified before any DB is
  minted.

## 10. Prespecified diagnosis (exploratory)

If the superseded session does outrank the current one at scale, the following
are inspected **in the trace**, before the scratch DB is dropped, and reported as
mechanism evidence rather than as a tested claim:

- Does supersession mint edges at all? `memory_edge` rows with
  `kind = 'supersedes'`, counted from the DB.
- Are retired units still `active`? `memory_unit.state` distribution, and whether
  any `Superseded` unit carries a null `transaction_to`.
- Recall exclusion: `crates/memphant-core/src/lib.rs` `unit_is_recallable`
  admits `Superseded && transaction_to.is_some()`, but `bitemporally_recallable`
  then closes it for a live `transaction_as_of` — so the exclusion is expected to
  hold. Verified against the data, not assumed.
- Is the hardcoded semantic-supersession path reachable at all on this corpus?
  It requires an **explicit subject** fact key; the same block comments
  "AUTO-KEYS NEVER SUPERSEDE".
- `retention_tier`: expected to have 0 readers and 0 writers, i.e. every episode
  `'hot'` forever. Confirmed from the DB column distribution.

The diagnosis is worth more than the score, and is explicitly not part of H1.
