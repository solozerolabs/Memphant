# Substrate Completion + Per-Type Accuracy Program

Date: 2026-07-30
Status: SPEC — awaiting owner approval. No checkbox moves on adoption.
Supersedes: nothing. **Extends** `docs/superpowers/plans/2026-07-27-accuracy-first-program.md`
(Phases 0–1 of which are DONE; its Phase 2/3 sequencing is amended by §7 here).
Method: substrate gap audit + technique/license scouting, both verified against
repo files and upstream LICENSE files; every load-bearing claim re-verified by
the author before being written here.

## 0. What changed to make this plan possible

**The reuse policy is now license-governed** (`26` §D-2026-07-30, `13` §0,
landed `ce5a07f0`). Permissively licensed code — MIT / Apache-2.0 / BSD /
PostgreSQL — may be adapted with attribution, competitors included. Copyleft and
non-commercial stay excluded. Competitor *products* remain excluded as runtime
dependencies: adapting Apache-2.0 source is reuse, depending on a hosted service
is a dependency, and MemPhant must stand up without anyone else's service
running. Model weights carry a license separate from their repository.

Single biggest consequence, verified: **`vectorize-io/hindsight` is MIT**
(LICENSE file, © 2025 Vectorize AI), and it is the one system in this space with
an **independent** reproduction of its LongMemEval standing (Virginia Tech
Sanghani Center + The Washington Post, arXiv:2512.12818). Its fusion logic is now
adaptable rather than merely observable.

## 1. The finding that gates everything else

**Track R's questions are lexically pointed at their targets, and BM25's win is
substantially an artifact of that.** Measured directly on the bank:

| measure | value |
|---|---|
| question → **target** event token coverage | mean **0.396**, median 0.388 |
| question → random **non-target** event, same attempt | mean **0.094**, median 0.064 |
| lexical concentration on target | **4.2×** |
| goldens where the question narrows to exactly one event | **105/180** |

The `identification` block records `distinguishing_terms` that are literally
target identifiers (`ignite`, `progressbar`, `tqdm_logger.py`), and the question
text embeds them. This is not miner sloppiness — it is the **preregistered
identification gate working as written**. "Questions must causally identify their
target" was satisfied by copying identifiers out of the target.

Independent corroboration that this is not what production queries look like:
**CLARC** (ICLR 2026, USC, third-party-run, 1,245 C/C++ pairs) measures BM25 at
**R@10 = 18.06** on genuine natural-language→code queries. Our BM25 scored
**0.8944** on the same metric class. A ~5× outlier against a third-party
measurement is a property of the instrument, not of BM25.

**Therefore the Phase 1 kill-gate verdict is hereby qualified**: MemPhant lost to
BM25 *on a bank whose construction favours lexical matching, with dense
embeddings switched off*. That is a real loss on a real instrument and it is not
dismissed — but it does **not** establish that BM25 beats MemPhant on
production-shaped queries, and **no ownership decision (question (d)) may be
taken on it** until W0 completes. Recorded so the 0.8944 is never cited as a
production-representative baseline.

It also cuts the other way, and this is why W1 survives: the three lexical
defects found in our own code are real independent of the bank.

## 2. Verified substrate reality

The ledger overstates what exists. Corrected in `ab3d720b`; the rest is recorded
here for the first time.

| Claim | Verified reality |
|---|---|
| Learned reranker BUILT | **False.** Deleted at `2cd157a4`; `grep -rn learned_rerank crates --include='*.rs'` → **0 hits** |
| Procedural harness BUILT | **Read-side only.** `Validated` required at `lib.rs:8292-8305`, but **no write path ever promotes to `Validated`** |
| Hot plane | **Absent.** `retention_tier` (`hot|warm|cold`, `bootstrap:278`) has **0 readers and 0 writers** in `crates/`. Every episode is `'hot'` forever |
| Cold plane / demotion | **Absent.** `cold`/`demote`/`demotion` appear in no Rust file. What exists is the inverse: hard `forget_memory` with tombstones |
| Typed write-router | **Absent.** One linear loop with an if/else ladder on `admission_hint`; `kind` enters only as data |
| Preference / Knowledge types | **Not nameable.** `MemoryKind` = `Episodic \| Semantic \| Procedural \| Belief \| Resource` (`memphant-types/src/lib.rs:968`) |
| RLS on the served path | **Dead weight.** `SET ROLE` appears **0 times outside `tests/`** (14 inside); no login role is a member of `memphant_app`; roles are `NOINHERIT`; no startup non-superuser assertion; every packaging path ships a bypassing role |
| File plane B2/B3 | **Genuinely BUILT** — best-evidenced work in the repo |
| ANN index | **Absent.** Zero `using hnsw`/`using ivfflat` in any migration |

**And the R3 governance core was never written as a spec.** Hot plane, cold
plane, and the typed write-router exist only in
`docs/reports/2026-07-11-prosumer-memory-campaign-report.md:231-275`. "Finish all
substrates" currently has **no written contract to finish against**. W3 fixes
that before any of it is built.

Of the 15 unchecked STATUS items: **4 need code**, 5 need *measurement* on
machinery and corpora already in place, 4 should be **retired or rewritten**, 2
are blocked outside the repo. Detail in §6.

## 3. Corrections to previously reported standings

- **Temporal/state is not 32.96.** FAMA improved **32.96 → 53.49** on the
  reader-only replay of the complete 15-question split (`STATUS.md:46`) — but
  **raw unweighted accuracy stayed flat at 43/71** (pilot: 44/71). The honest
  standing is "official FAMA 53.49 via reasoning weighting, raw accuracy flat,
  root cause state maintenance."
- **The coding-domain correction-retention first-mover slot is CLOSED.**
  `aiming-lab/ClawArena` (**MIT**, arXiv 2604.04202, UNC/UCSC/Berkeley) runs a
  four-stage protocol where preferences emerge through user corrections and are
  then tested in **silent-exam rounds with no reminders**, 337 rounds with
  executable checkers and a public leaderboard. Earlier statements in this
  program that no such instrument exists are **withdrawn**.
- **PrefEval cannot be run.** Its LICENSE is Attribution-**NonCommercial** 4.0
  (GitHub's `NOASSERTION` is a detector failure). Its `<10%@10-turns` figure
  stays citable as published prior art; its dataset is unusable.
- **The number to beat is TRACE's, not PrefEval's.** arXiv 2606.13174 reports
  memory baselines leaving **100%** of held-out preference checks violated and
  **Mem0 at 57.5% violated**, with TRACE at **37.6% ID / 2.0% OOD**.
  Self-reported, not independently reproduced.
- **LongMemEval's split is deprecated upstream** in favour of
  `xiaowu0162/longmemeval-cleaned`. Part of our 0.56 may be an artifact of a
  deprecated split. Free to re-measure.

## 4. Workstreams

### W0 — Instrument validity (FREE, BLOCKING)

Nothing in W1 may be promoted before this completes, because W1 optimises
against instruments W0 validates.

- **W0.1 Track R paraphrase variant.** Re-mine a variant whose questions carry
  the *semantics* of identification without the *tokens* — paraphrased, with
  distinguishing identifiers withheld from the question and preserved only in the
  adjudication record. Target the same 150–200 size and the same shape mix.
  Preregister before mining. Report question→target coverage; the bar is that it
  approaches the non-target floor (~0.09), not the current 0.396.
- **W0.2 Re-run the three-arm probe** (scoped BM25 / MemPhant fused / MemPhant
  packed) on the paraphrase variant. **This, not the current bank, is the
  instrument that decides ownership question (d).**
- **W0.3 Re-measure the chat lane on `longmemeval-cleaned`.** Free, and it may
  move 0.56 on its own.
- **W0.4 Resolve the distractor-coverage threshold** (75/180 = 41.7% vs a 50%
  floor) with the §1 evidence in hand — the floor mis-modelled a gate that
  narrows to one event 105/180 times.
- **W0.5 Review the 15-golden Track R spot-check.** Until this is done **no
  number from this bank is publishable**, including any improvement W1 produces.

### W1 — Retrieval (the measured defects, real regardless of §1)

Three independent defects, all verified in code:

1. **No IDF and anti-calibrated length normalisation.** The final lexical score
   is **Jaccard token-set overlap** (`token_set_overlap_text_score`,
   `memphant-core/src/lib.rs:10645`): `intersection / union`, no term frequency,
   no IDF, and a union denominator that penalises long documents *monotonically*
   — the opposite of BM25's calibrated `b`.
2. **Identifier-destroying tokenisation.** `body_tsv` is
   `to_tsvector('english', body)` (`bootstrap:367`) — the English stemmer and
   stopword list applied to code. `to_tsvector` lowercases before splitting, so
   `FeatureStore` can never match `feature`; `tokenize` (`lib.rs:9880`) splits
   `snake_case` but not `camelCase`.
3. **Fusion demotes single-arm winners.** Weighted RRF (`lib.rs:10707`).

Adoptions, in order:

- **W1.1 Implement BM25 in-house** (k1/b tunable) over the existing candidate
  set, plus **ingest-time identifier expansion** (`getUserId` → `getUserId get
  user id`, original preserved) under a `simple` text config. Portable across
  Neon / Supabase / plain Postgres, no extension, no license question. Reference
  formula available under MIT (`Michael-JB/bm25`). Costs a re-index, not a
  migration.
- **W1.2 Replace weighted RRF with TM2C2 convex combination** — Bruch, Gai &
  Ingber, *An Analysis of Fusion Functions for Hybrid Retrieval*, **ACM TOIS
  2023** (arXiv 2210.11934). Peer-reviewed; authors Pinecone-affiliated, so
  flagged vendor-adjacent-but-refereed. Beats RRF on all tested datasets, more
  robust under domain shift. Paper-we-implement, zero dependency.
- **W1.3 Adapt Hindsight's interleave fusion** (MIT, attribution required):
  round-robin across arms so each arm's top candidates survive, instead of score
  aggregation that demotes items ranked highly by one arm only. Directly aimed at
  our in-pool-unpacked misses.
- **W1.4 Dense arm + hybrid** — never yet measured on the code lane.
- **W1.5 Packing displacement fix** — 56 of 147 top-10 golds displaced; the
  binding constraint is the k=10 slot limit, not the token budget
  (`budget_share_of_in_pool_unpacked` = 0.0118).

**Deferred, deliberately: `timescale/pg_textsearch`** (PostgreSQL License,
`text_config='simple'`, tunable k1/b, expression indexes) is the best-fitting
software found, but it requires `shared_preload_libraries` and PG17/18, which
**neither Neon nor Supabase offers** — and we ship provider profiles for both. It
belongs as a self-hosted fast path *after* W1.1 proves the accuracy gain, never
as the mechanism that delivers it.

**Rejected on license:** ParadeDB `pg_search` (**AGPL-3.0**, verified; also
withdrawn by Neon for new projects 2026-03-19), `VectorChord-bm25` (AGPLv3 /
ELv2), `bm25_turbo` (AGPL-3.0-only). **Rejected on architecture:** `tantivy`
(MIT, clean) — a second index outside Postgres reintroduces the store-divergence
anti-pattern this repo already has scar tissue from.

### W2 — Reader composition (largest measured gain available)

The chat lane is reader-bound (R@10 0.83–0.94, QA 0.56) and the benchmark's own
authors measured the fix under **oracle retrieval — our exact regime**:

- **W2.1 Chain-of-Note + structured JSON context format.** LongMemEval §5.5
  reports **up to +10 absolute points**, consistent across model sizes, at **zero
  extra LLM calls** — a single-call output-format change. Their GPT-4o
  LongMemEval-S baseline is **0.606**; our 0.56 sits just under it, i.e. we are
  at parity with an un-augmented reader and the composition gain is unclaimed.
  Take the prompting form only — Chain-of-Note's original paper (arXiv 2311.09210)
  involves GPT-4-distilled fine-tuning, which we do not want.
- **W2.2 Timestamp-aware composition** for temporally-classified queries: same
  source, **+11.3% recall (round-based) / +6.8% (session-based)**. Authors caveat
  that it needs a strong extractor model.
- **Rejected:** CRAG and HyDE reference implementations have **no LICENSE file**
  at all; Self-RAG is MIT but training-dependent and stale (last push 2024-05-25);
  RAG-Fusion has no canonical paper. Lost-in-the-middle reordering is **folklore
  worth one free A/B**, not an adoption — the specific recipe appears only in blog
  sources.

### W2b — External corroboration: ARC-AGI-3 (OpenAI, 2026-07-29)

OpenAI reports that two **context-management** settings tripled GPT-5.6 Sol's
ARC-AGI-3 public-set score — **13.3% → 38.3%** RHAE (estimated human tester
average 48%) — with **~6× fewer output tokens**, model unchanged. The two
settings:

1. **Retained reasoning** — the model's private chain-of-thought is preserved
   across turns (via `previous_response_id`) instead of discarded after every
   action. The official harness threw it away, so the model "was asked to figure
   out the game anew" each turn; it could see past moves, but not the plans and
   insights that produced them.
2. **Compaction** — when context fills, prior context is **summarized rather
   than truncated**. The official harness used rolling truncation, discarding
   oldest messages past a 175,000-character window.

This is billed as a reasoning result. It is a **memory result**: both settings
are about not throwing state away. Three consequences for this program.

**(a) It corroborates the chat-lane diagnosis independently.** We measured that
lane as reader/composition-bound (R@10 0.83–0.94 against QA 0.56), and the
LongMemEval authors measured up to +10 points from composition alone under oracle
retrieval. This is a third, larger datapoint in the same direction: with
retrieval held constant, how context is *carried and composed* moved the score
3×. It strengthens W2 relative to W1 in expected value.

**(b) Compaction is a direct candidate for the packing displacement (W1.5).**
Our packer's failure mode is structurally the same as the harness's: hard
discard under a cap. We drop 56 of 147 top-10 golds because the binding
constraint is the k=10 output-slot limit — a slot budget, hit with truncation.
"Summarize instead of discard" is the exact substitution that paid here. Adopt
it as an arm in W1.5, **not** as a default: compaction is an LLM call at compose
time, which is a real latency and cost line, and its benefit here was measured on
a single long episode with no retrieval available. MemPhant's thesis is that you
retrieve instead of summarize, so compaction competes with our own mechanism
rather than obviously complementing it — that is a measurement, not a
prediction. Note it does **not** violate deterministic-writes/no-LLM-at-ingest:
compose-time ≠ ingest-time.

**(c) Retained reasoning names a memory type we cannot store.** `MemoryKind` has
nothing for the agent's own prior reasoning or plan state — episodic records what
happened, not why the agent chose it. This is the same insight Track U encodes at
a slower timescale (corrections as rule + incident + how-to-apply bundles, never
bare triples). Fold into W3.2 alongside `Preference` and `Knowledge`.

**Evidence handling — binding.** The 38.3% is **OpenAI-self-run on their own
harness** and is not an ARC Prize leaderboard result; official Sol on ARC's board
is far lower, and other models' ARC-protocol numbers were produced under a
different harness. **We may cite the 13.3 → 38.3 delta as evidence about
harnesses, and never as a cross-model comparison** — comparing a
self-harnessed number against a leaderboard number is precisely the error our
same-lattice rule exists to prevent. OpenAI's closing recommendation, that
comparisons should use the settings they deploy, is a reasonable engineering
point and also self-serving; treat the mechanism as adoptable and the
comparative framing as unestablished.

**Turn it on ourselves first.** Their finding is that a benchmark number was
depressed by harness defaults nobody chose deliberately. We just ran the entire
code lane with `--embed-model off` and have **never** run a dense arm, and our
chat lane sits on a split its upstream has deprecated. W0 already covers both;
this raises their priority. Add a standing check: **every promotion-capable run
records its harness settings beside its score**, and a harness default is not
evidence merely because it was the default.

### W3 — Substrate completion (spec first, then code)

- **W3.1 Write the governance-core spec** into `04-memory-model-spec.md` /
  `14-ingestion-seeding-and-ops-spec.md`: typed write-router, hot/warm/cold
  planes, demotion-not-deletion. **This does not exist and must precede any
  implementation.**
- **W3.2 Extend `MemoryKind`** with `Preference` and `Knowledge`. Two of the four
  router types are currently unnameable, and Track U's 51 preference goldens have
  no kind to be stored as.
- **W3.3 Land the `memphant_app` served role** — highest value-per-hour item in
  the ledger, days of work. Needs: a login role that is a member of
  `memphant_app`; an explicit `SET ROLE` in `connect_pool`'s `after_connect` (the
  roles are `NOINHERIT`, so membership alone grants nothing); a startup assertion
  that the connected role is non-superuser with `rolbypassrls = false`; and
  corrected packaging paths (compose ships the initdb superuser; the Neon profile
  ships `memphant_owner`, which has a `using(true)` bypass policy). Until this
  lands, **RLS on the served path is decorative** and isolation rests entirely on
  application predicates.
- **W3.4 Make `retention_tier` real** — give the hot/warm/cold column a writer
  and a reader, i.e. actually build the hot plane, with the demotion path.
- **W3.5 Procedural write-side promoter** to `UnitState::Validated`, so something
  can pass the read gate that already enforces it.

### W4 — Per-type ladder

Each type gets an instrument before it gets a technique. Two types have no
instrument today, so for them "approach SOTA" begins by building the measuring
stick — and for correction retention there is no external number to be close to
beyond TRACE's self-reported one.

| Type | Instrument | Standing | Action |
|---|---|---|---|
| Episodic / chat | LME-S (**re-pin to cleaned**) | QA 0.56 vs field band 0.58–0.72; authors' GPT-4o baseline 0.606 | W2.1 + W2.2 + W0.3 |
| Repo / code | Track R + **W0.1 paraphrase variant** | fused 0.8167 vs BM25 0.8944 — **instrument-biased, §1** | W0.1/W0.2 then W1 |
| Semantic / docs | Syndai docs gate (dropped) | rerank win unreachable under top-16 compression | reopen only if W1.2/W1.3 change the 16–64 band story |
| Preference / user-learning | Track U (51) + **ClawArena** (MIT) | never scored | W3.2 first (no `Preference` kind), then score |
| Procedural | **STATE-Bench v0.8.0** — pinned, adapter built, 2,842 episodes mapped, **never run** | unmeasured | run it; W3.5 if the write-side gap binds |
| Forgetting / lifecycle | ForgetEval | 244/15/126 — ties Lethe v1, trails Mem0, **at zero model calls** where Mem0 pays per write | keep the deterministic advantage; treat as a cost-honest tie |
| Temporal / state | Memora FAMA | **53.49** official, raw accuracy flat 43/71; root cause state maintenance | state-aware exact-unit create/replace/delete |

Reranking is deliberately **not** on this ladder as an adoption. Two independent
2026 benchmarks measured reranker transfer to code as break-even-to-negative —
**CoREB** (arXiv 2605.04615: Jina v2 **−8.8%** code-to-code, Qwen3-4B +3.3%
code-to-code / −0.1% text-to-code) and **CORE-Bench** (arXiv 2606.11864: Qwen3-8B
71.7 nDCG@10 on snippet search vs **20.3** on issue→edit localization). This
corroborates our own weak-transfer prior. The only sanctioned move is **one cheap
decisive measurement**: `bge-reranker-v2-m3` (weights **apache-2.0**, code MIT)
via **`fastembed-rs`** (Apache-2.0) — pure Rust ONNX, no Python sidecar, no GPU —
measuring p95 *and* nDCG, then letting that number decide whether a reranker lane
exists at all. Trap to avoid: two of fastembed-rs's four built-in rerankers are
**Jina CC-BY-NC** weights — a permissive library shipping non-commercial
defaults. `bge-reranker-v2.5-gemma2-lightweight` is likewise rejected: MIT repo,
**Gemma-licensed weights**.

### W5 — Launch path

The public launch gate **does not depend on rungs 5–15**. Real dependencies, in
order: (1) W3.3 `memphant_app`; (2) **WS-G real public surface** — all seven
advertised routes currently render from a 187-line static fixture, and this is
the long pole, weeks; (3) reconcile the restraint instrument — `27` §1 and the
scorecard name OP-Bench/PS-Bench while
`tests/test_restraint_launch_gate.py:35` asserts
`benchmark in {"op-bench","ps-bench"}`, so **a passing MemSyco run would fail the
gate's own contract test** ($0 fix); (4) run the full MemSyco restraint suite
(smoke was 5/5 at $0.236); (5) one reproduced public benchmark profile on the
packaged runtime — the single binding accuracy criterion; (6) re-derive
`public-launch-scorecard.json` off `invalid_synthetic_fixture`.

## 5. Retire, don't finish

- **Rung 12 (L4 Deep)** — A1 proved **0/166** dev Fast-misses are depth-bound.
  The row's "still missing: paired evidence where Deep recovers Fast misses" is
  stale; there are none to recover.
- **Rung 13 (learned rerank)** — mechanism deleted at `2cd157a4`; no training-data
  floor. Retire rather than rebuild.
- **Rung 6 (edge expansion)** — measured-negative and structurally blocked: edges
  mint only from supersession/derivation, never from chat episodes.
- **GateMem** — R90 makes it a DORMANT row, not a launch blocker.

## 6. Adoption register (licenses verified from LICENSE files)

| Item | License | Verdict |
|---|---|---|
| `vectorize-io/hindsight` fusion | MIT | **ADAPT** with attribution |
| TM2C2 fusion (TOIS 2023) | paper | **IMPLEMENT** |
| BM25 formula (`Michael-JB/bm25`) | MIT | **IMPLEMENT** (~200 lines) |
| Chain-of-Note prompting form | paper | **IMPLEMENT** (prompting only, not the fine-tuning) |
| ClawArena | MIT — **owner-directed** (see §6c) | **ADOPT**, pending data verification |
| `YujunZhou/TRACE_exp` | MIT | **EVALUATE** — corrections as executable gates |
| tree-sitter + cAST chunking | MIT / paper | **ADOPT** for the code lane (costs a full re-chunk/re-embed) |
| Zoekt ranking design | Apache-2.0 | **IMPLEMENT** (Go — patterns, not code) |
| `bge-reranker-v2-m3` via `fastembed-rs` | apache-2.0 weights / Apache-2.0 crate | **EVALUATE ONCE** |
| `nomic-ai/CodeRankEmbed` | MIT weights | **EVALUATE** — only CPU-viable permissive code embedder |
| `timescale/pg_textsearch` | PostgreSQL | **DEFER** — breaks Neon/Supabase profiles |
| ParadeDB `pg_search`, VectorChord-bm25, `bm25_turbo` | AGPL / ELv2 | **REJECT** |
| Jina rerankers/embeddings incl. v3.5, SFR-Embedding-Code, Qodo-Embed-1 | CC-BY-NC / RAIL | **REJECT** |
| PrefEval | CC-BY-**NC** | **REJECT** the data; **rebuild the design** (Track U) |
| `voyage-code-3` | hosted API | **REJECT** — no weights; product dependency |

**Nothing above is a capability block** (`26` §D-2026-07-30b). A restrictive
licence blocks *their code*, never the behaviour. Every AGPL/ELv2 row here is a
BM25 implementation, and BM25 is a published 1994 formula — W1.1 already
implements it from the description, so those rejections cost us nothing but a
dependency we did not want. Same for fusion: TM2C2 and interleave are described
in a refereed paper and in public code comments respectively.

The two genuine limits, stated rather than wished away: **model weights cannot be
reimplemented** — a CC-BY-NC reranker is not a technique we can rebuild, only a
permissive alternative or our own training run — and **datasets cannot be
reimplemented, though their methodology is free**, which is exactly why Track U
is a legitimate answer to PrefEval and its data is not. Clean room also gives no
patent defence.

## 7. Sequencing and budget

1. **W0** (free, blocking) — instrument validity, cleaned-split re-measure,
   spot-check review.
2. **W2.1** (cheap) — the largest measured gain in the program, at zero extra
   calls per query.
3. **W1** — retrieval, promoted only against W0-validated instruments.
4. **W3.1–W3.3** — spec, kinds, served role.
5. **W4** — per-type, starting with the free STATE-Bench run.
6. **W5** — launch path.

**Amendment to the 2026-07-27 plan:** its Phase 3 recommendation ("do not launch
as designed") **stands but for an amended reason** — not because the substrate
lost, but because the instrument that produced the loss is not
production-representative. Phase 2 remains dropped in priority on the Phase 1b
null. Ownership question (d) is **reopened and undecided**, pending W0.2.

Every standing measurement rule is unchanged: promotion-provenance, paired
preregistered same-lattice evidence, scratch DBs, $0 gates before paid spend,
never fabricate a number, and **"SOTA" stays banned until a protocol run**.
Adopted code earns its default through the same bar as code we wrote; a
technique's published gain is never our number.

## 6a. Dataset sweep corrections (2026-07-31) — three beliefs in this plan were wrong

A public-dataset sweep (HF, Kaggle, GitHub, Zenodo, lab sites), verifying licenses
from LICENSE files and record metadata and **shipped rows rather than READMEs**,
overturned three things asserted above.

**(1) The "no public coding-memory instrument" premise fails — three exist.**

| instrument | license (verified) | what it carries |
|---|---|---|
| `AMA-bench/AMA-bench` | **MIT** (card metadata; the paper text says CC-BY-4.0 — a real disagreement to resolve) | 208 episodes / 2,496 QA; **36 episodes are SWE-bench run through OpenHands, with 432 human-annotated QA pairs** typed recall / causal / state-update / state-abstraction, turn-anchored |
| `CohereLabsCommunity/memorycode` | **Apache-2.0** (card + GitHub) | 8,400 sessions, **2,913 `instruction-update` events**, and **4,908 sessions ship deterministic regex graders** — correction retention scored programmatically, no LLM judge |
| `yifannnwu/SWE-Together` | **Apache-2.0** | 109 tasks from 11,260 real user-agent sessions; 804 typed `oracle_intents` including **103 real corrections** with `source_turn` and verbatim excerpts |

**AMA-Bench is the closest public analogue of Track R** — same corpus family
(SWE-bench via OpenHands), human-annotated memory QA, MIT, free. Running it first
would have given a human-authored calibration baseline **before** we generated a
single question, and the bias would have been visible immediately. Adoption cost
is a schema adapter plus accepting n=36 episodes.

**(2) ClawArena's MIT is a badge, not a verified license — and we had already
adopted it.** `aiming-lab/ClawArena` returns **HTTP 401** and is not a public HF
dataset. The data is at `Haonian/ClawArena`, whose `cardData.license` is **null**
with **no LICENSE file**; the MIT claim is a shields.io badge pointing at a
relative `LICENSE` that does not exist there. MIT is confirmed only on the GitHub
*code* repo. This is exactly the failure mode our own rules warn about, and it
landed on the instrument this plan adopted for Track U. **Status: HOLD.** Better-
governed sibling to evaluate: `TokenRhythm/Claw-SWE-Bench` (350 tasks, real MIT
LICENSE file, DATASHEET.md). Also recorded: `SWE-bench_Verified` declares **no
license at all** on either org.

**(3) Our leakage bar was set below the human floor.** Measured against
human-authored coding query sets with same-domain hard negatives:

| set | q→target | q→hard-neg | ratio |
|---|---:|---:|---:|
| **our mined bank** | **0.396** | 0.094 | **4.21×** |
| AMA-Bench software QA | 0.287 | 0.148 | 1.94× |
| SWE-rebench issues | 0.269 | 0.143 | 1.88× |
| SWE-PRBench review comments | 0.197 | 0.112 | 1.76× |
| SWE-bench-Live issues | 0.175 | 0.086 | 2.03× |

Humans occupy **1.76–2.03×**, so the preregistered **≤1.50 concentration bar was
unachievable by construction**. The paraphrase re-mine's 2.05 sits *inside* the
human band: a mis-specified bar, not a bad bank.

Two consequences. First, **prefer absolute target coverage as the headline
metric** — against *random-corpus* negatives human sets also score ~3.70×, so the
ratio is sensitive to negative selection while the absolute (0.396 vs a human
0.175–0.287) is robust. Second, the paraphrase bank's **0.135 is below the human
range**, so it may be **harder than reality** and W0.2's survival ratio should be
read as a lower bound. Related: real human issues name the gold file path only
**22.2%** of the time (111/500).

**Human-review corpora at volume** (the owner's repos hold only ~15 human review
comments): `foundry-ai/swe-prbench` CC-BY-4.0, 3,093 human review comments with
path/line/diffHunk — **3.3% are bot-authored despite the card claiming otherwise,
filter by author**; Microsoft CodeReviewer (Zenodo 6900648, CC-BY-4.0) ~120k+
human review→change triples; `zhangfw123/CORE-Bench` Level-2, 5,061 queries /
52,712 qrels derived **mechanically from merged patches** — but `license` is
**null**, so unresolved, and the `Rewrite-*` configs are LLM-rewritten and
excluded; CodeSearchNet `annotationStore.csv` (MIT), 4,006 pooled human judgments.

**Verified rejections:** `Tomo-Melb/CodeReviewQA` gated (401);
`JetBrains-Research/agent-trajectories-swe-bench` ships `resolved` null in 300/300
rows; `THUIR/MemoryBench` ships 4 blank columns; `CodeReviewSE` ships scripts and
no data; `yoonholee/terminalbench-trajectories` has unresolved `$N` placeholders;
CoIR's CodeSearchNet subsets are docstring↔code pairs — maximal lexical overlap by
construction, the exact bias we are escaping. Long-Horizon-Terminal-Bench
(Apache-2.0) withholds its verifiers (`tests/` ships 0 files) — a harness, not data.

**One genuine open slot remains:** no public CLAUDE.md/AGENTS.md
convention-adherence benchmark exists. That search came back clean.

## 6b. GitHub lane — no bank certified, and the leakage metric is mis-specified

**Verdict: three preregistered bars FAIL; no GitHub-lane bank ships.** The
bar-clearing slice is 13 goldens (S2 revert/supersession + S3 fix-of-a-fix)
against a ≥40 floor, and Syndai holds 90.4% of private non-S4 goldens against a
≤60% cap. Recorded in `benchmarks/data/github_lane_golden.lock.json`
(`composition_bars_all_pass: false`). No threshold was moved.

**The human stratum is not thin — it is empty.** All 15 "human" review comments
in Syndai are the owner replying to CodeRabbit, 11 of them
`Addressed in <sha>: …` — the actor describing his own change, which is the Track
R defect arriving through a different door. The 16 RecMe issues are open,
zero-comment backlog tickets in a repo with no CI. **S5 yield: 0.** The private
repos cannot supply human-authored queries at any scale.

### The metric conflates two different properties

The miner found a mis-specification in its own bar and **deliberately did not
apply the fix**, logging it as bar-shopping to fix after seeing an inconvenient
number. That was the right call, and the finding is correct:

**Concentration detects *copying*, which requires the query to be writable from
the target.** A CI runner emits failure text before the fix exists; a reviewer
writes against the pre-change hunk. Neither *can* have copied from its target, so
S1's 3.31× and P1's 2.42× are not contamination.

So the metric is measuring two distinct things under one number:

| property | meaning | discriminator |
|---|---|---|
| **Contamination** | the query was authored *from* the target — the number is **fake** | **provenance**: was the query authored before the target existed? |
| **Lexical tractability** | the query naturally shares tokens — the number is **real but narrow** | the statistic itself |

Provenance is a fact, not a statistic, and it settles contamination outright.
Track R fails on provenance (an LLM read the target and wrote the question).
CI-failure and reviewer queries pass on provenance regardless of their
concentration — a real user pasting a stack trace does name the failing file.

But lexical tractability still matters and is **not** dismissed: a high-
concentration bank, however honestly built, measures only the lexical regime and
cannot separate lexical from semantic retrieval quality. That is precisely how
Track R made dense embeddings look worthless. Both properties get reported; only
contamination is disqualifying.

**Action:** a separately preregistered instrument that reports provenance class
and concentration as two fields, never one gate. Not applied retroactively here.

**Separately, a genuine construction bug to fix (not bar-shopping):** S1 targets
repeat each filename three times (file list, `--stat`, diff header), inflating
concentration artificially. P1 targets are whole file-level diff sections rather
than the reviewed hunk. Both are target-rendering defects, fixable on their own
merits.

### The pattern: our leakage gates keep coming in stricter than reality

Second instance today. The Track R paraphrase bar was set at ≤1.50 when
human-authored queries occupy 1.76–2.03×. Now a **published, human-authored
corpus** — `foundry-ai/swe-prbench` — **fails our gate at 2.42×**. When a human
corpus fails a gate, that is evidence about the gate. Calibrate leakage bars
against measured human baselines before preregistering them, not against the
intuition that lower is better.

**Public corpora, independently re-verified:** swe-prbench 350 PRs / 3,093
comments, with **102 bot-authored (3.30%) across 37 PRs despite
`ai_comments_removed: 0`** — filtered by author, exactly as the sweep warned.
Microsoft CodeReviewer **deferred**: license verified CC-BY-4.0, but the corpus
predates 2022 and carries uncontrollable contamination risk. CORE-Bench
**blocked** on its null license — nothing vendored. CodeSearchNet deferred as the
wrong shape.

**Secrets:** 2 candidates dropped whole (`anthropic_key`,
`generic_secret_assignment`) — never redacted-and-kept, and no matched value
written to any artifact. All five source repos left at their original HEADs.

## 6c. ClawArena — licence settled by owner decision (2026-07-31)

**Decision (owner): treat ClawArena as MIT and proceed.** The §6a HOLD is lifted.
Adoption now turns on **data quality only**.

Recorded so the basis stays traceable rather than looking like an oversight: the
GitHub *code* repo carries a real MIT LICENSE file; the HF dataset side carries a
shields.io badge and no LICENSE file, and `aiming-lab/ClawArena` returns HTTP 401
while the data is reachable at `Haonian/ClawArena`. The lock records
`license: "MIT"` with `license_provenance` naming this decision and its date.

Practical exposure is narrow: the risk attaches to **redistribution and published
claims**, not to internal measurement. Mitigation adopted, and it costs nothing —
**ClawArena content is pinned and mirrored locally, never committed and never
redistributed; only the lock is committed.** That keeps the decision reversible if
the upstream licence is ever clarified against us, and it is the same handling the
private banks already get.

Still required before it measures anything, on the same standard as every other
instrument: verify the shipped rows — that the 337 evaluation rounds exist, that
the executable checkers actually ship and run rather than being withheld (the
Long-Horizon-Terminal-Bench failure mode), that the corrections are
human-authored, and that the scored fields are populated. `TokenRhythm/Claw-SWE-Bench`
is evaluated alongside on data quality alone, no longer as a licensing fallback.
A data-quality rejection remains available; a licence rejection does not.
