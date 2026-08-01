# SWE-ContextBench re-tranche: the instrument is live, and we killed it on an n=4 draw

**Date** 2026-08-01 · **Branch** `w1-swecb` · **HEAD at acquisition** `89cc22c45bac2cda20129c1efa6427f08a2dbf8b` · **dirty** no
**Model calls executed** 0 · **Settled cost** $0.00 · **Paid steps authorized** none

## Verdict up front

| Question | Answer |
| --- | --- |
| Is SWE-ContextBench saturated? | **No.** Published no-memory baselines are **19.68%** (Related, n=376) and **26.26%** (Related Lite, n=99). |
| Was the kill verdict wrong? | **Wrong about the instrument, right about the tranche.** n=4 baselines cannot establish a baseline rate. |
| Which split? | **`SWEContextBench_Related`, all 376 rows, no selection.** |
| Is Lite (n=99) usable? | **No — underpowered.** MDE 7.93–15.93pp. So is the published Table 4 on which Supermemory's "win" rests. |
| Licence | **DECLARED-ONLY, MIT, record metadata. No LICENSE file exists anywhere. Not redistribution-clear.** |
| Cost to a neutral public number | **~$545** ($0 stage 0, ~$40 psi pilot, ~$504 measurement; honest band $440–$780). |

---

## 1. Acquisition and hash verification ($0, done)

The parquets were missing from disk. Re-downloaded at the pinned revision
`5bec275a2095768a53ac804ae4fdf90b1723b8af` from
`https://huggingface.co/datasets/jiayuanz3/SWEContextBench/resolve/<rev>/<path>`, and mirrored at
`~/.memphant-private/w7-instruments/swe-contextbench/` alongside `memorycode`, `ama-bench`,
`clawarena` and `claw-swe-bench` (never-delete cache convention, `AGENTS.md:35-41`).

All three sha256 recorded by `swe_contextbench.kill.n12.json` **match exactly**, byte counts included:

| file | bytes | sha256 | vs kill manifest |
| --- | --- | --- | --- |
| `data/SWEContextBench_Lite_Experience.parquet` | 1,119,540 | `7a21f37b…3551b4` | **MATCH** |
| `data/SWEContextBench_Related_Lite.parquet` | 538,469 | `1930b392…4a68b8f3` | **MATCH** |
| `data/SWEContextBench_Relationship.parquet` | 24,385 | `4bcbe816…09843ce93` | **MATCH** |
| `data/SWEContextBench_Experience.parquet` | 4,027,938 | `384aa652…73dfa7b9` | never pinned before |
| `data/SWEContextBench_Related.parquet` | 2,250,460 | `890aaf0f…deaeca2ad` | never pinned before |
| `README.md` | 4,661 | `be87da6d…6c45a4ca51` | never pinned before |

**The kill manifest never acquired the recommended evaluation split.** `SWEContextBench_Related`
(376 rows) and the full `SWEContextBench_Experience` pool (1,100 rows) were not among the three
files it hashed. Tranche 1 was built entirely out of Lite.

Promoted to `benchmarks/manifests/swe_contextbench.lock.json` (revision, per-file sha256, splits,
mirror path, licence provenance, attribution, power, staging, preregistered pilot ids).
`swe_contextbench.kill.n12.json` is **retained unchanged** as the record of the failed tranche.

## 2. Licence — resolved as far as reality permits, and no further

**I did not read an MIT licence, because there is no MIT licence to read.**

What exists:
- HF record metadata at the pinned revision: `cardData.license = "mit"`, tag `license:mit`. This is
  the platform's structured field, not a shields.io badge and not README prose.
- The card `README.md` carries YAML frontmatter `license: mit`, and that file is now pinned by
  sha256, so the declaration is content-addressed.

What does not exist:
- **No `LICENSE` in the HF dataset repo.** Six files ship at the pinned revision; a direct GET of
  `/resolve/<rev>/LICENSE` returns **404**.
- **No `LICENSE` in `github.com/jiayuanz3/SWEContextBench`.** The recursive git-tree API returns
  **1095 entries, `truncated: false`**, with zero paths matching `/licen/i`; the licence API returns
  **404**. The kill manifest's `NO_LICENSE_FILE_OBSERVED` is independently re-confirmed, now against
  the whole tree rather than the licence endpoint alone.
- The arXiv HTML states `License: CC BY 4.0` — that governs the **paper**, not the dataset or code.

**Recording.** `license_id: MIT`, `license_source: RECORD_METADATA`, `verified: false`. This clears
the `check_evidence_contract.py` guard that refuses `unverified` on a decisional artifact, and it
does so honestly: the schema's enum exists precisely to separate a machine-readable record field
from a badge, and `RECORD_METADATA` is what we actually have. It is **weaker than the
`memorycode.lock.json` standard**, which required a real fetched LICENSE corroborating the card.

**Consequences, stated so nobody has to rediscover them.** Usable for local measurement and for
citation. **Not clearable for redistribution** — no upstream data or code is vendored into this repo,
and the parquets live only in the out-of-repo mirror. Recommended owner action: open an issue asking
the author to add the LICENSE the card already declares.

## 3. Split enumeration — three of the "untested splits" do not exist

The complete file list at the pinned revision is five parquets. Prior MemPhant artifacts named
`SWEContextBench Full`, `Multilingual` and `Lite Past Experience` as untested splits. **No such
files exist.** `Full` and `Lite Past Experience` are informal names for the Experience pools;
multilingual content is not a split at all — it is mixed **inside** `SWEContextBench_Related`, whose
40 repos include 28 absent from Lite (Go, Rust, JS, PHP, Java, Ruby, C: `jqlang/jq`, `sharkdp/bat`,
`axios/axios`, `gin-gonic/gin`, `redis/redis`, `tokio-rs/tokio`, `laravel/framework`, `google/gson`,
`projectlombok/lombok`, `vuejs/core`, `rubocop/rubocop`, `apache/lucene`, …).

All counts below were counted from the pinned parquets with pyarrow, not read off the card.

| file | rows | role | repos | scorable |
| --- | --- | --- | --- | --- |
| `SWEContextBench_Experience` | 1,100 | memory pool | 53 | no |
| `SWEContextBench_Lite_Experience` | 300 | memory pool (⊂ Experience) | 12 | no |
| `SWEContextBench_Related` | **376** | **evaluation targets** | 40 | **yes** |
| `SWEContextBench_Related_Lite` | **99** | evaluation targets (⊂ Related) | 12 | **yes** |
| `SWEContextBench_Relationship` | 376 | related→experience edges | — | no |

Structural facts worth having: the edge table has 376 rows over **357** distinct related ids (19
tasks have 2 parents) and **229** distinct experience ids; `Related ∩ Experience` = 1 instance.

**Trap recorded.** The 99 Lite related tasks have **72** distinct experience parents, and **one of
those 72 is not in `Lite_Experience` (300)**. Running Lite against only the Lite pool silently
starves a target of its gold parent.

## 4. The saturation screen — $0, measured from published results, not assumed

**No agent was run.** The parquets ship **no baseline or resolve column**, so saturation is not
derivable from the data; it had to come from published results. Source: the benchmark's own paper,
arXiv:2602.08316v3, fetched 2026-08-01 (`arxiv.org/html/2602.08316v3`, fetched-HTML sha256
`ff7720e930184f165db2033b25a16978ffcb32bd69ee60c4f4610d2471ef1cef`) and independently re-extracted
by me from the raw HTML rather than taken on a subagent's word.

| split | n | no-memory resolve | source | ceiling reference | can express an effect at our MDE? |
| --- | --- | --- | --- | --- | --- |
| `Related` | **376** | **19.68%** | Table 3, `Claude Sonnet 4.5 / Claude Code`, Closed-source Baseline | Oracle Summary **23.40%** (+3.72pp) | **YES** |
| `Related_Lite` | **99** | **26.26%** | Table 4, `No-Context (Baseline)` | Oracle Summary **34.34%** (+8.08pp) | **NO — underpowered** |
| `Experience` / `Lite_Experience` | 1100 / 300 | n/a | pool, not targets | — | n/a |

Table 4's other rows (Lite, n=99): Free Context Learning 26.26, Oracle Context Learning 27.27, Free
Summary Learning 22.22, **Mem0 24.24**, **OpenViking 29.20**, **Supermemory 30.30**.
Table 3's other baselines (Related, n=376): GPT-5.3 Codex 22.60, MiniMax 2.7 19.68, GLM-5 18.35,
Qwen3.5-plus 16.22. Scaffold for the recommended row: **Claude Code, Claude Sonnet 4.5**.

A split whose no-memory baseline resolves 19.68% has **80pp of headroom**. It is not saturated.

### Correction to the framing I was given

**The 26.26 / 30.30 / 24.24 figures are not a third-party run.** They are rows of Table 4 of the
benchmark authors' own paper. There is no Supermemory blog post, no vendor reproduction, and **no
leaderboard**. `supermemory.ai/research` does not mention the benchmark. Nine citing papers were
fetched and grepped; every mention is a related-work sentence and none reports a run.
(`contextbench.github.io` / arXiv 2602.05892 is a *different* benchmark, "ContextBench" — search
engines blend them. Do not cite it.)

This is **better** for the claim, not worse: the comparators are published and citable, and **nobody
has taken the first-mover slot**. It also means our run would be the second run of this instrument
in existence, so the scaffold must match the paper's or the comparison is void.

### The thing that actually matters: no per-instance results exist

`github.com/jiayuanz3/SWEContextBench` has a `predictions/` directory containing only `.DS_Store`,
no `results/`, no `logs/`, no resolved-id lists — 1095 tree entries, nothing results-shaped. **We
cannot pair against any published arm.** Every MemPhant arm must be paired against a MemPhant arm we
run ourselves on the same instances. Comparison to the published rows is unpaired and cross-run, and
must be labelled as such wherever it appears.

## 5. Power — and why Lite must be refused

Two-sided exact McNemar, α=0.05, power 0.80, via `scripts/instrument_power.py`
(sha256 `1a2ffbe1…31da58`). **ψ is UNVERIFIED** — MemPhant has never run a paired arm here, and per
the script's own rule no ψ is borrowed from another lane. This is a grid, not a point estimate.
Lower bound ψ ≥ 0.0404 (= the largest published margin); upper bound under independence 0.4065.

| ψ | MDE @ n=99 | MDE @ n=376 | MDE @ n=1100 |
| --- | --- | --- | --- |
| 0.05 | **unreachable at any effect size** | 3.30pp | 1.94pp |
| 0.08 | 7.93pp | 4.23pp | 2.46pp |
| 0.10 | 8.88pp | 4.72pp | 2.74pp |
| 0.15 | 11.05pp | 5.74pp | 3.34pp |
| 0.20 | 12.83pp | 6.64pp | 3.85pp |
| 0.30 | 15.93pp | 8.11pp | 4.70pp |

Power at the program's D_MIN = 7pp: **n=376 → 0.99 / 0.94 / 0.84 / 0.67** at ψ = 0.10 / 0.15 / 0.20 /
0.30. **n=99 → 0.50 / 0.35 / 0.27 / 0.19.** Required n for D_MIN: 166 / 260 / 340 / 505.
Expected discordant pairs clear the n_d ≥ 6 floor comfortably at n=376 (30–113) and only barely at
n=99 (8–30).

**Table 4 is not a measurement, by this program's own floor.** Detecting Supermemory's 4.04pp margin
at n=99 has power **0.08–0.15**; required n is **510–1485**. Supermemory 30/99 versus baseline 26/99
is a four-instance difference. We must not cite that ranking as a measurement, and we must not try to
beat it at n=99 — a win there would be exactly as meaningless as theirs.

## 6. Recommended tranche

**`SWEContextBench_Related`, all 376 rows. No selection is performed.**

Hand-selection is what produced the saturated n=12 tranche. The census-complete split is the only
rule that is trivially reproducible from the lock and cannot be gamed: the instance list *is*
`890aaf0fc82a739e5031232d81e208b82d71798c86d1960725055ffdeaeca2ad`.

- **MDE at this n:** 4.23pp (ψ=0.08) to 8.11pp (ψ=0.30); 4.72pp at ψ=0.10.
- **Arms:** `no_memory` and `memphant_memory`, both run by us. Same 376 instances, same scaffold,
  same model, same grader invocation, retrieval at the same pipeline stage.
- **Primary endpoint:** Resolved (%) by the official grader (`evaluation.sh` →
  `swebench_memory.harness.run_evaluation`), F2P/P2P as shipped.
- **Secondary (preregistered as secondary, never the headline):** FAIL_TO_PASS test rate. Table 4
  shows memory moving F2P 19.64 → 55.95 while moving Resolved only 26.26 → 30.30, because Patch-N/A
  rises 3.03 → 10.10 and P2P falls 94.91 → 90.23. Memory finds the right code and then breaks the
  patch. That is a MemPhant-actionable finding and a higher-powered endpoint, but the SOTA claim is
  Resolved.
- **Decision rule, preregistered:** reject H0 iff two-sided exact McNemar p ≤ 0.05 **and** n_d ≥ 6
  **and** every scored instance carries a mechanism-liveness receipt. If n_d < 6 the artifact says
  **NOT A MEASUREMENT** and states the required n — never "a tie", never "no effect". No arm is
  scored until both arms are complete. No post-hoc instance drops except logged grader-infrastructure
  failures, applied to **both** arms.
- **Scaffold pin (a deviation requiring explicit authorization):** **Claude Code + Claude Sonnet 4.5**,
  because that is Table 3's baseline row. Any other scaffold forfeits comparability to the only
  published baseline. This departs from the program's usual OpenRouter engine and **has never been
  round-tripped by us**.

### Experience pool: take the free option

**Option A (recommended, $0):** ingest the 1,100 Experience rows' `problem_statement` + `patch` +
`hints_text` + `created_at` directly as MemPhant episodes. Safe w.r.t. the target: experience
patches are merged PRs that predate their related target and are shipped as the pool by
construction. The target's `patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS` stay hidden, as in
the kill manifest's `hidden_target_fields`. **Caveat to state in any writeup:** this is *not* the
paper's pool (theirs is agent trajectories, avg 25,633.7 tokens, or gold summaries, avg 217.1
tokens). Gold-patch ingest is a different and probably stronger memory, and makes our arm
non-identical to Table 3's oracle-summary row.

**Option B (rejected, ~$737):** rebuild the trajectory pool as the paper does — 1,100 tasks ×
$0.67/task. Triples the price to buy fidelity to a construction we are not claiming to reproduce.

## 7. Go / no-go and price

| stage | what | cost | status |
| --- | --- | --- | --- |
| **0** | $0 adapter contract round-trip | **$0** (one sub-dollar liveness call) | **GO — do this next, no authorization needed** |
| **1** | ψ pilot, n=30, both arms | **~$40** | **NO-GO pending stage 0 green** |
| **2** | the measurement, n=376, both arms | **~$504** (band $440–$780) | **NO-GO pending stage 0 + stage 1 ψ** |

**Total to a neutral, public, powered SOTA number: ~$545.**

Cost basis is **measured, not estimated**: Table 3's `Cost ($)` column gives $0.67/task for the
Claude Sonnet 4.5 / Claude Code baseline on Related; Table 4's Lite costs span $0.53–$0.98/task. The
full Related split includes non-Python repos absent from the Lite cost basis, so plan the upper end.
Docker: 752 container runs at stage 2, local compute, no cloud spend.

**Stage 0 acceptance (program rule 7 — three adapters failed at first contact, two after money was
authorized, one would have billed $211–634 for zero rows):**

1. Pull 3 `jiayuanz3/swecontextbench` images for Related instances; record digests.
2. Official grader on the **shipped gold patch** for those 3 → must report resolved.
3. Official grader on an **empty patch** for those 3 → must report unresolved.
4. Ingest the 1,100 Experience rows into an ephemeral MemPhant DB (`with_scratch_db`, local
   fastembed) and recall each of the 376 target `problem_statement`s; record recall@k of the true
   Relationship parent. $0, local, and **a real retrieval measurement in its own right** — if MemPhant
   cannot retrieve the gold parent, no amount of agent spend will produce an effect and stage 1 is
   cancelled for free.
5. Drive the pinned scaffold end-to-end on **one** instance with a hard token cap. Liveness probe,
   **not a measurement**.

## 8. What changed in the repo

- **NEW** `benchmarks/manifests/swe_contextbench.lock.json` — the promoted lock.
- **KEPT** `benchmarks/manifests/swe_contextbench.kill.n12.json` — unchanged, provenance of the
  failed tranche.
- **AMENDED** `benchmarks/manifests/instrument_register.json` — the `repo-code-swe-contextbench`
  entry's verdict `BROKEN (baseline-saturated)` is scoped to tranche 1 and the instrument is
  re-listed as live-but-never-validly-run.
- **AMENDED** `scripts/instrument_power.py` — the lane note "Baseline-saturated … max possible gain
  1 < required 2" is corrected to name the tranche, and the regenerated `instrument_power.json`
  follows.

## 9. Standing cautions

- **ψ is unverified and must not be assumed.** Every MDE above is conditional on it. Stage 1 exists
  only to measure it.
- **Agent runs are stochastic.** Table 4's `Free Context Learning` row is *exactly* 26.26, identical
  to the baseline, which is either a coincidence or a hint about run-to-run variance. A same-arm
  re-run leg belongs in stage 1: discordance between two *identical* arms is pure noise and bounds
  how much of any observed ψ is signal.
- **Do not cite Table 4's framework ranking as a measurement**, ours or theirs.
- **Do not conflate SWE-ContextBench (2602.08316) with ContextBench (2602.05892).**
- **The licence is declared-only.** Local measurement and citation are fine; redistribution is not.

---

# S5 stage 0: the gate is GREEN, the instrument is smaller than advertised, and stage 2 cannot be powered

**Date** 2026-08-01 · **Branch** `s5-swecb` · **Branch point** `0e874da0` · **Model calls** 0 · **Settled cost** $0.00
**Preregistration** `docs/build-log/artifacts/s5-swecb/stage0-prereg.json`, committed at `fd2ebd7c` **before the first ingest**, amended at `0e137b54` **before any cell existed**.

## Verdict up front

| Question | Answer |
| --- | --- |
| Can MemPhant retrieve the official Relationship parent? | **Yes. Packed recall@5 = 0.759** over 357 targets against a 1,007-row pool. |
| Stage 0 gate | **GREEN** (preregistered band: GREEN ≥ 0.50). Stage 1 proceeds. |
| Where do the remaining misses live? | **Ranking, not packing and not candidate generation.** Of 86 misses at k=5: 74 ranked below the cut, 8 retrieved-but-unpacked, 4 never a candidate. |
| Is the tranche 376? | **No. It is 357.** Confirmed four independent ways. |
| Was the "$0 gold-patch pool" safe? | **No.** It leaks the answer on ~30% of targets. Changed to a patch-free pool before any cell existed. |
| Is stage 2 worth $504? | **No, as currently scoped.** The instrument's own ceiling effect is 3.72pp and its MDE at max n is 3.38–8.32pp. |

## 1. The gate — GREEN, and it is a real measurement

`scripts/swecb_stage0_recall.py`, artifact `docs/build-log/artifacts/s5-swecb/stage0-recall-patchfree.json`,
lineage `0809249d`, worktree clean, local embeddings, ephemeral scratch DB, **0 model calls, $0.00**.

Every one of the 1,007 distinct experiences is bound to **one** subject and **one** scope, and all 376
Related rows are queried against that same context. This is the structural difference from the
retained n=12 rehearsal, which bound a fresh scope per `(target, arm)` and therefore ranked over a
pool of **one** — its citation assertion could not fail, and it was never a retrieval measurement.

**Primary view — 357 distinct tasks, ANY-PARENT, patch-free pool:**

| k | packed recall@k (what the agent would see) | retrieval recall@k (trace `fused_rank`) |
| --- | --- | --- |
| 1 | 0.5602 | 0.5602 |
| 3 | 0.7143 | 0.7283 |
| **5** | **0.7591** | 0.7787 |
| 10 | 0.8291 | 0.8459 |
| 25 | 0.8824 | 0.9048 |

Row census (376 rows, the published n): packed recall@5 = 0.7553. The two views agree to 0.4pp, so
the duplication does not move the number — it moves what the number is *of*.

**Miss taxonomy at k=5, over 357 tasks.** This is the part that tells us what to fix:

| class | n | share of misses |
| --- | --- | --- |
| hit | 271 | — |
| **ranked below the cut** | **74** | **86.0%** |
| retrieved but not packed | 8 | 9.3% |
| never a candidate | 4 | 4.7% |

The retriever surfaces ~550 candidates per query out of 1,007 and the gold parent is in that set for
353 of 357 targets. **The bottleneck is ranking.** It is not candidate generation (98.9% coverage)
and it is not the packing budget (9.3% of misses) — so `pack_render_cap`, the lever the rung-7
verdict handed us, is worth at most ~2pp here. A reranker is the lever that matters, and this is a
clean, free, 357-question bench to evaluate one on.

**Difficulty scales with haystack, as it should.** `django/django` (n=84, 345 same-repo pool rows)
scores 0.679; repos with a handful of siblings score 1.0. Any aggregate here averages over an
order-of-magnitude spread in per-target difficulty.

**Mechanism liveness, all asserted in code with the run aborting on failure:** 1,007 retains, 1,007
distinct episode ids, worker completed 1,007, queue drained **verified on the bench credential**
(pending=0, dead=0) and not the worker's self-report, 1,007/1,007 instances carrying a compiled
memory unit, 1,053 memory units, 1,053 embeddings, **0 degraded recalls**, 376/376 traces fetched.
Latency p50 1,398 ms, p95 4,368 ms, max 29,876 ms. Wall 1,112 s.

## 2. Stage 0 has published comparators — the retranche log was right about Resolved and wrong about retrieval

Table 5 of the paper reports **Matched (%)** — did the system retrieve the gold parent — per method
on Lite. That is *this* endpoint. Banked at `docs/build-log/artifacts/s5-swecb/published-comparators.json`.

| method | k | overall Matched (%) | pool |
| --- | --- | --- | --- |
| Free Context Learning | self-determine | 18.18 | 300 |
| Free Summary Learning | self-determine | 36.36 | 300 |
| Mem0 | 3 | 39.39 | 300 |
| OpenViking | 3 | 51.52 | 300 |
| Supermemory | 15 | 59.60 | 300 |
| LangMem | 10 | 73.34 | 300 |
| **MemPhant (this run)** | **5** | **75.91** | **1,007** |

**Every caveat must travel with that last row.** Each published method chose its own k and the table
does not normalise it. Their pool is 300 rows; ours is 1,007, which is a harder retrieval problem.
n=99 there versus 357 here. These are the benchmark authors' runs of other people's systems with no
per-instance detail published, so **nothing can be paired and no significance test is possible**.
A like-for-like Lite-scoped arm is running; until it lands, the row above is suggestive, not a claim.

## 3. The tranche is 357, not 376 — confirmed four independent ways

`SWEContextBench_Related.parquet` has **376 rows over 357 distinct `instance_id`s**. The 19
duplicated ids are **not** byte-identical: they differ in `version` (one of each pair is null),
`problem_statement` (11), `PASS_TO_PASS` (12), `FAIL_TO_PASS` (8), and one pair differs in `patch`,
`test_patch` and `base_commit`.

The complete explanation: the split is **Lite (99) ⊎ Verified (166) ⊎ Multilingual (111) = 376**,
concatenated, and **Lite ∩ Verified is exactly those 19 ids**. Corroboration:

1. distinct `instance_id`s in the parquet: **357**
2. Docker Hub `jiayuanz3/swecontextbench`: **357 instance tags** plus one `base`
3. official repo `cases/SWEContextBench Full/`: **357** case files
4. the sub-split arithmetic above

`SWEContextBench_Experience.parquet` is likewise **1,100 rows over 1,007 distinct ids** (93
duplicate groups, all byte-identical). The paper's "1,476 tasks" is **1,364** distinct.

**Correction to §3 of the retranche log above.** It states that `SWEContextBench Full`,
`Multilingual` and `Lite Past Experience` do not exist. They exist — as case directories in the
**code** repo rather than parquets in the **dataset** repo, at pinned rev `31bb0415`: Full 357,
Verified 166, Multilingual 111, Lite 99, Lite Past Experience 300.

**Correction to the edge-table claim.** "19 tasks have 2 parents" is wrong. There are 376 edges over
**360 distinct (target, parent) pairs**; only **three** targets have two *distinct* parents
(`django__django-27910`, `scikit-learn__scikit-learn-25763`, `scikit-learn__scikit-learn-25365`).
The other 16 surplus edges are repeated pairs, 14 of them byte-identical rows. The ANY-PARENT vs
ALL-PARENT choice governs 3 tasks, not 19.

## 4. The "$0 gold-patch pool" leaks the answer — changed before any cell existed

The task brief and §6 of the retranche log both hold that gold patches "predate their targets by
construction, so no target leakage". **Both halves are false.**

**Temporal.** Parsing both `created_at` formats properly, of 376 edges only **131** have a parent
that strictly predates its target; **120** are exact ties and **125** postdate. And the Related
split's `created_at` is not trustworthy at all: within a repo it inverts against PR number on
**23.6%** of pairs (1,423 of 6,022), where the Experience pool inverts on **0 of 60,372**. The
honest statement is that the temporal relation **cannot be established from the shipped metadata**.

**Patch overlap — which needs no timestamps and is the load-bearing evidence:**

| measure | gold parent | same-repo random non-parent |
| --- | --- | --- |
| touches ≥1 target patch file | **75.5%** | 9.1% |
| identical touched-file set | 32.4% | — |
| contains an exact target added line (>20 chars) | **37.2%** | — |
| shares ≥50% of target added lines | 29.8% | — |
| mean added-line overlap | **0.2856** | 0.0013 |

For roughly a third of targets the gold patch **is** substantially the answer diff, at 220× the
random control. Ingesting it is not a "probably stronger memory"; it is an answer key.

**The patch-free body is clean.** Parent `problem_statement` + `hints_text` contains an exact target
added line on **9.0%** of edges, against a **6.9%** floor set by the target's *own* problem statement
quoting its *own* patch. That is the instrument's irreducible noise. Admissible, and still $0.

**Consequence for the plan.** The `$0` pool in the staging table is inadmissible as specified. The
admissible $0 pool is patch-free prose, which is what the GREEN number above was measured on.

## 5. Two live defects, both caught for $0

1. **HTTP 422 `observed_at must use a UTC offset`.** 300 of the 1,007 experience rows ship
   `created_at` as `YYYY-MM-DD HH:MM:SS` with no timezone; 707 ship `...Z`. The split is exactly the
   41 multilingual repos versus the 12 original SWE-bench Python repos, **zero repo overlap** — two
   upstream pipelines concatenated into one file. A naive adapter dies 30% through the pool.
2. **`rc` from the wrong end of a pipe.** The first invocation printed `EXITCODE=0` for a run that
   had died with a traceback, because the status came from the trailing `tail`. The repo's own
   hazard notes say to capture `rc` first; the runner now does, and asserts a non-empty artifact.

Both are exactly the class stage 1 exists to find, found before any money moved.

## 6. Stage 2 cannot be powered — and this is independent of MemPhant

The decisive numbers are the benchmark's own. Table 3, `Claude Sonnet 4.5 / Claude Code` on Related:
**no-memory 19.68%, Oracle Summary 23.40%**. That **+3.72pp** is the lift the authors measured for an
arm *handed* the gold parent. It is the ceiling for any retrieval-based memory system on this split
with this scaffold.

Two-sided exact McNemar, α=0.05, at the maximum n this split can offer (**357**):

| ψ | power for the 3.72pp ceiling | power for our expected 2.82pp | MDE at n=357 |
| --- | --- | --- | --- |
| 0.05 | 0.887 | 0.609 | 3.38pp |
| 0.10 | 0.546 | 0.330 | 4.85pp |
| 0.15 | 0.395 | 0.239 | 5.89pp |
| 0.20 | 0.308 | 0.189 | 6.81pp |
| 0.25 | 0.255 | 0.159 | 7.61pp |
| 0.30 | 0.220 | 0.140 | 8.32pp |

Our expected effect is `recall × ceiling = 0.759 × 3.72pp = ` **2.82pp**, and that is an *upper*
bound — it assumes a retrieved parent helps exactly as much as an oracle-supplied one.

**2.82pp is below the MDE at every ψ, including ψ = 0.05.** For an agentic coding benchmark ψ is not
0.05: under independence at these rates ψ = 0.339, and Table 3 itself hints at the noise (two
different models scoring *exactly* 19.68, and Free Context Learning scoring *exactly* the 26.26
baseline). At a realistic ψ = 0.15–0.30, power to detect our expected effect is **0.14–0.24**.

**The instrument is too small for the effect it measures.** That is true for us, and it is equally
true for the benchmark's own Table 4 ranking and for Supermemory's 4.04pp margin at n=99. Spending
$504 to run it would buy a non-significant result whose cause we already know, and reporting it
would violate this programme's own n_d floor in spirit if not in letter.

## 7. Resource facts stage 2 planning did not have

- **357 official images × 1.27 GB mean = ~453 GB** of Docker pulls. This host has **220 GB** free.
  A census run requires a serialized pull → run → `rmi` loop, not a warm cache.
- **No Claude Code scaffold exists in this repo.** Pinning to Claude Code + Claude Sonnet 4.5 —
  required for comparability to Table 3 — is unbuilt integration work that was never costed.
- **The paper's trajectory pool is public and free.** `cases/SWEContextBench Lite Past Experience/`
  holds 300 `.jsonl` Claude Code session transcripts (~236 KB each). The "~$737 to rebuild the
  trajectory pool" line applies only to the 707 non-Lite experiences, not to all 1,100.

## 8. Recommendation

**Stage 0: GREEN, complete, $0.** Retrieval is live and strong, and the miss profile names the
lever (ranking, not packing).

**Stage 1 (~$40): proceed, but re-scope it.** Its value is no longer ψ estimation for a stage 2 that
should not happen. Its value is (a) the Claude Code + MemPhant integration round trip, which has
never been done and is the largest unpriced risk in the plan, and (b) the same-arm re-run leg that
measures how much of ψ is pure agent noise — which is the number that would settle §6 empirically
rather than by argument.

**Stage 2 ($504): recommend CANCEL as scoped, independently of s4-controls.** The gate the brief
placed on s4 ("if we cannot beat grep for $40 we will not beat no-memory for $545") is sound, but it
is no longer the binding constraint: even a *perfect* MemPhant cannot produce a resolvable effect on
this split's `Resolved` endpoint at n=357.

**What to do instead, in priority order:**

1. **Re-scope the primary endpoint to FAIL_TO_PASS test rate.** Table 4 moves F2P 19.64 → 55.95
   while moving Resolved only 26.26 → 30.30. It is ~10× the effect and measured per test rather than
   per task, so it is the endpoint this instrument can actually resolve. It is already preregistered
   here as secondary; promoting it is a decision the owner should take explicitly, not a drift.
2. **Publish the retrieval result.** Stage 0 is a neutral, public, high-mindshare instrument measured
   against published per-method comparators, at $0, on the endpoint MemPhant actually claims. It is
   the strongest coding-memory evidence this programme holds, and it cost nothing.
3. **Publish the instrument audit.** 376→357, 1,100→1,007, the two `created_at` formats, the
   unreliable Related `created_at`, and the 37.2% gold-patch answer leak are findings the benchmark's
   own authors would want, and they establish neutral-instrument competence better than a
   non-significant $504 number would.

## 9. Endpoint adjudication — one endpoint is powered, and it is not `Resolved`

Stage 0's GREEN result made it worth asking which endpoint this instrument can resolve at all.
Ceiling = published baseline → best memory arm; expected = ceiling × our measured recall (0.7591).

| endpoint | baseline → best memory | ceiling | expected | verdict at n=357 |
| --- | --- | --- | --- | --- |
| `Resolved` | 26.26 → 30.30 (Lite) / 19.68 → 23.40 (Related) | 4.04 / **3.72pp** | **2.82pp** | **NOT RESOLVABLE** — below the MDE at every ψ |
| **`FAIL_TO_PASS` Tasks** | 29.29 → 40.40 | **11.11pp** | **8.43pp** | **RESOLVABLE**, power **0.75–0.99** across all ψ ∈ [0.15, 0.40] |
| `Patch N/A` | 3.03 → 10.10 | 7.07pp | 5.37pp | borderline; diagnostic, not primary |
| `PASS_TO_PASS` Tasks | 88.89 → 88.89 | 0.00pp | — | no effect to measure |

**A trap worth naming.** Table 4's `FAIL_TO_PASS` **Tests** column moves 19.64 → 55.95 — ten times
the `Resolved` effect — and it is tempting to adopt it. It should not be used. Tests are clustered
within tasks: a correct patch passes all of a task's F2P tests at once, so the per-test n is not an
independent n and the apparent effect is inflated by within-task correlation. The per-**task** column
(29.29 → 40.40) is the honest version of the same signal, and it is the one costed above.

## 10. Stage 1 adjudication — cancel as scoped, replace with a ~$1 probe

**What decision does $40 change? None, as scoped.**

Its preregistered purpose was ψ estimation for stage 2. That purpose is void twice over. For
`Resolved`, stage 2 is cancelled, so sizing it sizes a run that will not happen. For
`FAIL_TO_PASS` Tasks, **power is 0.75–0.99 across the entire plausible ψ range**, so knowing ψ does
not change the go/no-go. And the pilot could not have delivered it regardless: at n=30 the expected
discordant pairs are 4.5 (ψ=0.15) to 9.0 (ψ=0.30) — below this programme's own n_d ≥ 6 floor at the
lower end — with power 0.12 for even the 11.11pp ceiling.

**The other purpose is real and mispriced by 40×.** No Claude Code scaffold exists in this
repository. Pinning to Claude Code + Claude Sonnet 4.5 is mandatory for comparability to Table 3 and
has never been round-tripped by us. This programme has had three adapters fail at first contact, two
after money was authorised, one of which would have billed $211–634 for zero rows. That risk is
unretired — but retiring it costs about **$1**, not $40, and the lock already specifies the
instrument: drive the pinned scaffold end-to-end on one instance with a hard token cap.

**Recommendation.**
- **Cancel stage 1 as scoped** ($40, n=30).
- **Replace with a ~$1–$5 scaffold liveness probe:** 1–3 Related instances, Claude Code + Claude
  Sonnet 4.5, hard token cap, official grader on the shipped gold patch (must resolve) and on an
  empty patch (must not resolve), plus one MemPhant round trip proving retrieved context reaches the
  agent. **Decision it changes:** whether a re-scoped stage 2 is executable at all.
- **Cancel stage 2 on `Resolved`, permanently.** Not a tranche problem — 357 is the whole split.
- **Re-scoping stage 2 to `FAIL_TO_PASS` Tasks is an owner decision, not a lane decision**, because
  it changes what the headline is about. It is powered; `Resolved` is not. If the owner wants the
  `Resolved` headline specifically, the honest answer is that **this instrument cannot supply it at
  any budget**.

Spend authority is standing and is deliberately **not exercised** here beyond the ~$1 probe, because
nothing below the re-scope decision changes a decision.

## 11. The same shape as S6 and S7, from the other side

This is the third instrument in the programme to fail on a structural property rather than on
MemPhant's quality — and the first to fail from the **power** side rather than the **baseline** side.
S7 killed MDN browser-compat-data because a 20-line `scoped_interval` rule scores 1.0000 on every
band; S6 found as-of saturation at 0.9064. Both are corpora whose gold is computable from the fact
statements themselves, so a short rule saturates the baseline.

SWE-ContextBench is the complement. Its baseline is nowhere near saturated — 19.68% leaves 80pp of
headroom, which is exactly why the retranche verdict called it live. But the headroom **a memory
system can address** is only 3.72pp, and that is smaller than the instrument's own minimum
detectable effect at its maximum n. **Saturation kills an instrument from below; an effect ceiling
under the MDE kills it from above.** Both are properties of the instrument rather than of the system
under test, both are checkable for $0 before any spend, and neither is visible from a baseline rate
alone. The retranche analysis checked the baseline and stopped there; the ceiling check is the one
that should have been paired with it.
