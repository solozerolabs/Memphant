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
