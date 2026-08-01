# A discriminating instrument — where retiring the wrong rule is expensive

**Task S2 · branch `s2-instrument` · 2026-08-01 · $0, no paid call on any path.**

---

## 0. THE TRAP, first, because it is the finding most likely to be misread

**A corpus whose gold is not recency-identified will make BOTH arms score worse.**
The tempting read will be *"supersession regressed"*. It will not have.

The current +3.01pp (`2026-08-01-a-recency-control.md` §8.4) is measured on
MemoryCode, where the gold is the latest declaring session **by construction**
(`scripts/external_instrument_adapter.py:166` — `current_index, current_topic =
occurrences[-1]`, distractors `occurrences[:-1]`, `observed_at` assigned in
session order at `:81`). The A-recency control selects `max(observed_at)` per
`fact_key`; the bitemporal arm keeps the newest generation open. **Both arms
compute the gold rule.** The measured margin is the residual between two
implementations of the same correct rule, sitting near a ceiling by
construction.

Move to an instrument where currency is *not* recency and both levels fall.
That is the instrument working, not the substrate failing.

**Anyone who runs any instrument in this document must preregister, before the
first arm executes:**

1. **A DROP in absolute level, in both arms, is the EXPECTED outcome.** A level
   drop is not evidence about supersession and must not be reported as one.
2. **The endpoint is the GAP BETWEEN ARMS, not either level.** The
   preregistered decision quantity is `Δ = LSW(bitemporal) − LSW(A-recency)`
   with its cluster-bootstrap CI, and nothing else.
3. **The direction of the prediction must be written down before the run.** The
   hypothesis these instruments test is that **Δ GROWS** relative to the +0.0301
   measured on MemoryCode, because the trivial control loses the free ride. If Δ
   shrinks or crosses zero, the bitemporal machinery is not buying what §8.5
   claims and the exit price in plan §5 is back on the table.
4. **Report both levels and Δ in the same table, with the MemoryCode levels
   beside them**, so no reader can quote a level in isolation.

This program has committed three near-inversions by citing a stale control
(`a-recency` §8.6), by mis-specifying an ablation (`b1` §8.1), and by reporting
a gold-rule caveat too far upstream of the verdict that carried it (`a-recency`
§8.9). **The fourth is available here and it is a level-vs-gap confusion.**

---

## 1. Why powering MemoryCode harder is the wrong spend — restated with the census recomputed

Two independent structural facts, both verified in this worktree:

**(i) The gold is recency-identified.** Verified by reading
`scripts/external_instrument_adapter.py:130-180`, not by citing §8.8.

**(ii) Wrong retirement is nearly free.** From `2026-08-01-b1-structured-supersession.md`
§9.9, quoting §3.1's committed table at τ = 0.25: of **309 edges, 16 retire a
gold (5.2%)**; 293 retire a distractor or nothing. The metric distance between a
careless selector and a careful one is structurally small *whatever either
understands*.

**The consequence is a bound, not a noise level.** More probes buy a tighter
interval around a compressed number. This is why S2 exists.

### 1.1 A census run here, at $0, that sharpens (ii) and hands us the fix

Recomputed from the pinned parquet
(`~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet`,
sha256 pinned in `benchmarks/manifests/memorycode.lock.json`), using the
adapter's own key rule (`QUOTED.sub("<X>", topic).strip()`):

| quantity | value | note |
|---|---:|---|
| probe-bearing groups (a key declared ≥2×) | **1,063** | reproduces the lock exactly |
| total declarations inside those groups | **4,679** | |
| current-vs-stale pairs, Σ(m−1) | **3,616** | reproduces the lock's 3,616 |
| **as-of probes with a strictly later declaring session** | **3,608** | the usable non-degenerate set |
| degenerate (final declaration shares the session index) | 8 | excluded |
| contributing instances | **257** | mean 14.04 probes/instance, max 71 |
| declarations-per-key histogram | 2:277 · 3:193 · 4:133 · 5:133 · 6:92 · 7:83 · 8:152 | caps at 8 |
| **keys where a value is RE-ASSERTED after being replaced** | **0** | **regime (a) is structurally absent from MemoryCode** |

The zero is a real finding and it is load-bearing: **MemoryCode contains no
re-assertion at all.** A convention's value never reverts. So MemoryCode cannot
be re-cut to produce regime (a) at any price, and any instrument for (a) must
come from elsewhere.

---

## 2. The four regimes, and what each one falsifies

| regime | definition | what it falsifies that MemoryCode cannot |
|---|---|---|
| **(a) re-assertion** | a retired rule is later re-asserted; the latest declarer is then the *correct* target | that retire-and-forget is sufficient; requires reversible retirement |
| **(b) non-recency currency** | currency signalled by explicit deprecation, source authority/rank, or scope/branch | that `max(observed_at)` is the resolution rule at all |
| **(c) bounded validity** | valid-from/valid-to intervals; the question is indexed to a time T | **valid-time vs transaction-time separation** — the exact machinery A-recency has no analogue for |
| **(d) expensive wrong retirement** | retiring the wrong prior costs a large, measurable amount | that target *selection* is worth anything (B1's open question) |

---

## 3. The candidate survey

**Licence method, and it is graded strictly.** **[F]** an actual LICENSE /
COPYING file or a Zenodo licence record was fetched, with the licence name and
copyright line read · **[A]** the GitHub API licence object · **[C]** a
HuggingFace `cardData.license` field or a README badge — *a claim by an
uploader, not a licence artifact* · **ABSENT** the artifact was verified missing
(what 404'd is named) · **UNVERIFIED**. **A guess appears nowhere in this
document. `UNVERIFIED` and `ABSENT` are answers.**

Prior audits in this program found a benchmark whose licence was a shields.io
badge over a file that does not exist, and another whose HF card and repo
LICENSE contradict each other. Both traps recur below.

### 3.0 Six licences re-fetched by hand in this worktree, not delegated

| artifact | URL fetched | result |
|---|---|---|
| CronKGQA | `raw.githubusercontent.com/apoorvumang/CronKGQA/main/LICENSE` | **[F] MIT, "Copyright (c) 2021 Apoorv Umang"** — upgrades a prior [A] grade |
| **VersiCode** | `raw.githubusercontent.com/wutong8023/VersiCode/{main,master}/LICENSE` | **[F] Apache-2.0** on both branches |
| VersiCode HF card | `huggingface.co/datasets/AstoneNg/VersiCode/raw/main/README.md` | front matter reads **`license: mit`** — **CONTRADICTS the repo.** Trust the fetched Apache-2.0 file; record the contradiction |
| CodeUpdateArena | `raw.githubusercontent.com/leo-liuzy/CodeUpdateArena/main/LICENSE` | **[F] MIT**, copyright line literally `Copyright (c) 2024 <anonymized>` — a real Apache/MIT text with an **uninstantiated rightsholder**. Permissive in form, unattributable in fact |
| RoundEdit / ConflictEdit | `raw.githubusercontent.com/zjunlp/PitfallsKnowledgeEditing/main/LICENSE` | **[F] MIT, "Copyright (c) 2023 ZJUNLP"** |
| RippleEdits | `raw.githubusercontent.com/edenbiran/RippleEdits/master/LICENSE.txt` | **[F] MIT, "Copyright (c) 2023 Eden Biran"** — note the file is `LICENSE.txt` on `master`; `/main/LICENSE` 404s, so **a naive check reports ABSENT and is wrong** |
| **MDN browser-compat-data** | `raw.githubusercontent.com/mdn/browser-compat-data/main/LICENSE` | **[F] CC0 1.0 Universal.** The GitHub API object agrees (`CC0-1.0`). **No contradiction anywhere** — the cleanest licence in this survey |
| GitHub Advisory Database | `raw.githubusercontent.com/github/advisory-database/main/LICENSE.md` | **[F] CC-BY-4.0** ("Attribution 4.0 International") |
| ESLint | `raw.githubusercontent.com/eslint/eslint/main/LICENSE` | **[F] MIT, "Copyright OpenJS Foundation and other contributors"** |

**A path-shape lesson worth keeping.** Four of seven licence paths guessed from
a repo name were wrong, and each wrong guess produces a **false ABSENT**:
RippleEdits ships `LICENSE.txt` not `LICENSE`; FreshQA is `freshllms/freshqa`
not `google-research/FreshLLMs`; WikiBigEdit is `ExplainableML/WikiBigEdit`
(`lukasthede/WikiBigEdit` is the HF *dataset*); HyTE's branch is `master`.
**A 404 on a guessed path is not evidence of an absent licence.** Our ABSENT
grades below are only from paths that were actually located.

### 3.1 Ranked candidates

Ranked by *how much of the S2 question they answer*, then by licence safety.

| # | instrument | regimes | size (source) | licence | why it ranks here |
|---|---|---|---|---|---|
| **1** | **MemoryCode as-of re-cut** (§4) — *build, from material we already hold* | **(c) (d)** · not (a): **0 re-assertions measured** | **3,608** as-of probes / 257 instances (counted here from the pinned parquet) | **[F] Apache-2.0** — already adjudicated SAFE; real LICENSE read at `Cohere-Labs-Community/MemoryCode` | The only candidate that is $0, needs no new licence, reuses a pinned corpus and an existing harness, and is **paired against our own banked MemoryCode numbers** so the level-drop is directly interpretable |
| **2** | **MDN `browser-compat-data`** — *build; the coding answer* (§4b) | **(a) (b) (c) (d) — all four, in the CODING domain** | 20,243 features · 284,845 support statements · **7,454 carrying both `version_added` and `version_removed`** · 1,152 `status.deprecated` · **325 features / 704 browser-pairs with strict removed-then-re-added** · 17 runtimes (all counted from `data.json` v8.0.8) | **[F] CC0-1.0** — fetched here, *"CC0 1.0 Universal"*; **the GitHub API object agrees.** No card-vs-file contradiction, no attribution burden, one 19 MB JSON | **BCD has no timestamp axis at all.** Currency is keyed to **scope** — which runtime, which version — so `max(timestamp)` is not a weak baseline, it is **undefined**. Canonical case: `AbortSignal.timeout()` is live in Node 16.14, **dead in 17.0** (17.x branched before the backport), **live again from 17.3** — the newest assertion is the *wrong* answer for a Node-17.0 query and the right one is recoverable only from branch context. **And wrong retirement is EXECUTABLE**: retire a live fact and the agent emits a needless polyfill; keep a retired one and the generated code throws at runtime. Both are scoreable by running the code |
| **3** | **Wikidata direct extraction** — *build* | **(a) (b) (c) (d) — all four, the only source that has all four** | ≥959,026 P39 statements carrying **both** P580 and P582 (measured live; the corpus-wide count **timed out at 60s and is UNVERIFIED**) | **[F] CC0-1.0**, fetched from `Wikidata:Licensing`: *"All structured data in the main, property and lexeme namespaces is made available under the Creative Commons CC0 License"* — **no attribution obligation** | Re-assertion confirmed with QIDs (Q41421/P54/Q128109: 1984–1993 **and** 1995–1998; Q35171/P39/Q11696: 1885–1889 **and** 1893–1897). Non-recency currency confirmed with QIDs (Q12191/P131: the **deprecated**-rank statement holds the **latest** start date, 1800; the correct answer is a **preferred**-rank statement starting 1790). Extraction measured at **5,000 tuples / 0.84 s** |
| **4** | **VersiCode** | **(a) (b)** + **coding domain** | **49,346** items in the `new_to_old` file; **30 of 83** rename pairs run in **both** directions | **[F] Apache-2.0** (repo, both branches, fetched here) — **HF card says `mit`; contradiction, trust the repo.** Embeds StackOverflow content ⇒ **CC-BY-SA carry-over risk on redistribution** | The **only** artifact found that combines re-assertion, non-recency currency and code. `edit_order: "new_to_old"` makes the *older* API the correct answer; `type: name_change_old` / `delete` are explicit deprecation labels; `version: "==0.13.0"` is a scope signal, not a timestamp |
| **5** | **ChroKnowBench** | **(a) (b)** + a **matched unaffected-neighbourhood control** | General_Dynamic **n=8,330**, **23% A→B→A re-assertion**, **latest-wins correct on only 48.7%** of known (fact,year) cells; General_Static n=8,302, 0% change, latest-wins 100% (all measured on the shipped files) | **[C] only** — HF `cardData: cc-by-4.0`; **LICENSE ABSENT** (404 at `/raw/main/LICENSE`); code repo `dmis-lab/ChroKnowledge` carries **no licence object** | Highest measured re-assertion density of any live artifact, **and** ships a same-relation static twin (all 8 relations, 576 subjects shared) that is a free wrong-retirement control. Blocked on licence hygiene: measure locally, do not redistribute |
| **6** | **CronQuestions** | **(a) (c)** with questions already attached | **12,438** genuinely non-contiguous `(s,p,o)` of 284,892 distinct, in a 328,635-fact KG; 410,000 questions. **The slice is cleanly filterable**: joining `annotation{head,tail}` + `relations` against the KG TSV, **4,467 of the 30,000 test questions (14.9%) have a gold triple that is itself non-contiguous** — densest in `time_join` at **54.6%**, thinnest in `first_last` at 5.8% | Code **[F] MIT, "Copyright (c) 2021 Apoorv Umang"** (fetched here). **Data zip: ABSENT** — no licence file inside the archive; upstream is Wikidata (CC0) | The only corpus with re-assertion **and** a question set. Use `data_v2` only — v1 has 31,951/410k placeholder-substitution errors per its own README. Repo dead since 2022-02; entities are bare QIDs |
| **7** | **TempLAMA** | **(b)-by-measurement**: `max(timestamp)` is correct on only **55.5%** of rows (measured on the shipped files) | **50,310** queries (10,693/4,654/34,963, counted); 5,839 subject-relation series, **96.8% change at least once**, **3.5% A→B→A** | **[F] Apache-2.0** (`google-research/language`); data on GCS, HTTP 200 | Ships `most_recent_answer` and `most_frequent_answer` as named baseline fields — i.e. **the trivial baselines are shipped with the data**, which is exactly the discipline this program keeps failing to apply. **See §3.3: it is defeated by a different trivial rule** |
| **8** | **RoundEdit** (+ ConflictEdit) | **(a) explicitly constructed** (e₁: o₁→o\*, then e₂: o\*→**o₁**) **+ (d)** | 2,500 easy + 2,500 hard | **[F] MIT, "Copyright (c) 2023 ZJUNLP"** (fetched here) | The cleanest licence of anything carrying **both** (a) and (d). `Distortion` / `Ignore Rate` / `Failure Rate` score suppression of the still-valid siblings of the edited fact — collateral forgetting, priced. Synthetic and model-editing-framed |
| **9** | **RippleEdits** | **(d)**, and (a) at prompt level | 4,755 edits; `Forgetfulness` non-empty on **779/1,922** | **[F] MIT, "Copyright (c) 2023 Eden Biran"** (fetched here, `master/LICENSE.txt`) | `Forgetfulness` is the sharpest single metric found: the **gold answer *is* the retired object**, reached by a negative-scope prompt. `Relation_Specificity` prices over-retirement directly |
| **10** | **CodeUpdateArena** | **(d)** in the **coding** domain — the only one anywhere | 670 examples / 161 updates | **[F] MIT** — but copyright reads `Copyright (c) 2024 <anonymized>` | `SPass@k` is the **only** locality metric in any coding benchmark found. Small; synthetic updates |
| **11** | **AToKe** | **(c)** + the **most on-point (d) in existence** | 8,819 (AToKe-SE, counted) | **ABSENT** — 404 on `PKU-ONELab/ATOKE` and `Arvid-pku/ATOKE`, `LICENSE`/`.md`/`.txt`, both branches | `HES`/`HRS` score answers to explicitly historical prompts, so a latest-wins-and-delete store scores near zero there while scoring full credit on the current fact. **This is the exact shape of our defect.** Unlicensed ⇒ **a design template, not a dependency.** It also *excludes* (a) by construction |
| **12** | **WikiBigEdit** | **(d)** at scale, on **real** Wikidata diffs | 502,382 QA pairs, 8 timesteps Feb–Jul 2024 | **[F] Apache-2.0, "Copyright 2025 Lukas Thede"** (GitHub API reports NOASSERTION on header formatting; the file is Apache-2.0) | Named `Locality` metric over real churn rather than counterfactuals. Model-weight-editing framing to re-map |
| **13** | **Retraction Watch / Crossref** | **(a) real un-retirement** + **(b) publisher authority** | **71,496** rows: 66,062 Retraction · 3,585 Expression of concern · 1,499 Correction · **160 Reinstatement** (155 with usable `OriginalPaperDOI`) | **[C]** — Crossref prose says CC0/public domain, but **no licence artifact ships with the file** (GitLab `LICENSE` 404s, README has no licence line, Crossref's own metadata-licence page 404'd) | Live, free, continuously updated, and genuinely non-recency: currency is a **publisher-issued notice**, and 160 rows are literal un-retirements. Only 6 DOIs carry both rows, so arcs need a DOI join |
| **14** | **TAQA / TemporalAlignmentQA** | **(c)**, strongest of any QA set | 20,148 (10,148/1,000/9,000) | **[F] Apache-2.0** on the repo (copyright holder line **not instantiated**); **the paper says CC BY 4.0 — a second card-vs-artifact conflict** | Each question carries a literal **year → answer map** (field `answer`, year-string → answer-list), so a T-indexed gold is systematically not the latest fact. **Re-assertion measured over all 20,148 records, not sampled: 11,168 (55.4%)** have a value reappearing after absence — **but ~4.9k of those are recurring competitions** (an award won in 2000 and again in 2015), which is event recurrence, not state continuity. **The honest strict-construct subset is 6,273** (no award/medal/champion/winner/cup/prize wording), ideally intersected with `answer_per_year == 1.0`. Gap lengths are a long tail (2y:1,261 … 9y:684), not clustered at 1. GPT-4-generated from Wikipedia ⇒ contamination near-certain |
| **15** | **WhoQA · RAMDocs · FaithEval · ContraDoc** | **(b) by scope**, **(d) by all-answers-required** | 5,000 / 500 / 1,000-per-config / 449+442 | **[F] BSD-3-Clause (VinAI)** · **[F] MIT (Han Wang 2025)** · **[F] Apache-2.0 (Salesforce, `master/LICENSE.txt`)** · **[F] Apache-2.0** | Currency by **scope** (same-name entities, all answers concurrently true) rather than time. Dropping a still-valid answer is scored as failure. Cleanest licences in the survey; smallest constructs |
| **16** | **TOFU · RWKU · KnowUnDo · WMDP** (unlearning) | **(b)** non-temporal currency (ownership request, hazard policy, legal scope) + **(d)** | TOFU 4,000 · RWKU **11,379** adjacency-graded neighbour probes · WMDP 1,273/408/1,987 | TOFU **[F] MIT** (CMU Locus Lab 2024) · RWKU **[C] cc-by-4.0**, repo LICENSE **ABSENT** · KnowUnDo **[F] MIT** · WMDP **[F] MIT** (bio-forget corpus **GATED**) | TOFU is the only true **Pareto** formulation (Model Utility × Forget Quality) and the only one adoptable commercially without a lawyer. RWKU is the largest locality instrument found and the least licensable |

### 3.2 Verified traps — do not ingest, and the reason

| item | evidence |
|---|---|
| **YAGO11k, and anything derived from it (incl. TGQA)** | **Measured zero re-assertion**: 20,509 rows, 20,509 distinct `(s,p,o)`, fully collapsed. It will look like a temporal corpus and cannot express regime (a) at all |
| **MQuAKE** | **No locality metric**, despite the reputation — Edit-wise / Instance-wise / Multi-hop accuracy only. Licence is fine (**[F] MIT, Princeton NLP 2023**); the construct is not there |
| **AToKe** | **Excludes (a) by construction** — chains built "non-overlapping and non-contradictory" — and contains fabricated future facts to 2028. Licence ABSENT |
| **ConflictBank** | The strongest (b) construct in existence (staleness *cause* is a first-class label; temporal generator deliberately post-dates the wrong evidence so `max(timestamp)` scores **worse than ignoring dates**) — and it has **no licence of any grade**: repo 404 on both casings, HF `cardData` licence `null` |
| **ICEWS** | **No licence.** Harvard Dataverse API `license: null`; terms of use read *"made available with limited information on how it can be used"*. The MIT on the ATISE/TeRo redistributions does not cure this |
| **LibEvolutionEval** | **[F] CC-BY-NC 4.0** — non-commercial — and its own `THIRD-PARTY-NOTICES` 404s |
| **ELKEN** | **[F] CC BY-NC-SA 4.0.** Circulating summaries say "CC BY-SA"; the fetched file says NC-SA. Both NC **and** SA |
| **LoCoMo** | **[F] CC BY-NC 4.0**, and **the GitHub API misreports it as `NOASSERTION`** — an API-only check misses the NC term entirely |
| **CodeSyncBench** | Licence ABSENT, card `null`, **and** it redistributes verbatim GitHub bodies |
| **TIQ · MenatQA · TemporalWiki · GrowOVER · TempReason · TempQuestions · AKEW/WikiUpdate · KRE · FARM · CoTaEval · MemBench · MemoryArena · PM-Bench** | licence artifact **verified ABSENT** (null API object plus 404 on LICENSE/COPYING). MenatQA additionally ships **999 of the paper's claimed 2,853** samples. TIQ's official links are **dead** |
| **Test of Time (ToT)** | **[C] cc-by-4.0** claim with **no LICENSE file**, and the card adds a **no-training restriction the licence does not grant** |
| **`-truthy` Wikidata dumps** | Useless here, from the download page's own text: the truthy files *"encode the best statements … as single RDF triples (qualifiers and references are omitted)"* — they destroy **both** the interval and the rank signal. Only `-all` dumps or WDQS preserve them |
| **WIKIDATA12k as a shortcut** | The HyTE repo is Apache-2.0 but **the data files are not in it** (Google Drive), it carries **no rank information**, and it is ~40k rows. Building from WDQS directly is cheaper **and** strictly better |

### 3.3 The second trap this survey found — and it is new

**A corpus that defeats recency may be defeated by a different trivial rule.**

TempLAMA ships both baselines and they were measured on the shipped files:
`max(timestamp)` is correct on **55.5%** of rows, but **`most_frequent_answer`
is correct on 70.7%**. So an arm that beats the recency control on TempLAMA can
still be losing to *mode*.

**Every instrument adopted from this document must preregister its full trivial
baseline set, not just A-recency.** At minimum: `max(observed_at)`, mode of the
value distribution, first-declared, and — for as-of probes —
`max(observed_at ≤ t)`. This program has been surprised by a trivial baseline
three times (BM25 on the preference lane, the Syndai docs gate, A-recency's own
gold alignment). **The fourth is `argmax(count)`.**

Related, and worth stating because it disposes of a whole family: **MemoryAgentBench's
"conflict resolution" competency is `argmax(serial_number)` with the sort key
printed in the prompt** — the benchmark tells the agent that newer facts have
larger serial numbers. LongMemEval and BEAM have the same shape. Nine of the ten
memory-agent benchmarks surveyed score only the changed fact and **none of the
ten contains a single coding task.** The register's ABSENT verdict on this slot
is confirmed from a second direction.

### 3.4 Licence artifacts — exact locations, so no reader repeats a false ABSENT

| artifact | URL |
|---|---|
| CronKGQA (code) | `raw.githubusercontent.com/apoorvumang/CronKGQA/main/LICENSE` — **[F] MIT, © 2021 Apoorv Umang** |
| CronQuestions **data** | `drive.usercontent.google.com/download?id=1fe7-x7ChszqzczKncoZcpwmWc1PBq1_0&export=download&confirm=t` (80 MB). **All 21 archive entries enumerated: no LICENSE/COPYING/NOTICE anywhere. Data licence ABSENT** |
| MDN BCD | `raw.githubusercontent.com/mdn/browser-compat-data/main/LICENSE` — **[F] CC0-1.0** |
| GitHub Advisory DB | `raw.githubusercontent.com/github/advisory-database/main/LICENSE.md` — **[F] CC-BY-4.0** |
| ESLint | `raw.githubusercontent.com/eslint/eslint/main/LICENSE` — **[F] MIT, OpenJS Foundation** |
| VersiCode | `raw.githubusercontent.com/wutong8023/VersiCode/main/LICENSE` — **[F] Apache-2.0** (HF card contradicts: `mit`) |
| CodeUpdateArena | `raw.githubusercontent.com/leo-liuzy/CodeUpdateArena/main/LICENSE` — **[F] MIT**, `© 2024 <anonymized>` |
| RoundEdit / ConflictEdit | `raw.githubusercontent.com/zjunlp/PitfallsKnowledgeEditing/main/LICENSE` — **[F] MIT, © 2023 ZJUNLP** |
| RippleEdits | `raw.githubusercontent.com/edenbiran/RippleEdits/main/LICENSE.txt` — **[F] MIT, © 2023 Eden Biran**. *A second, separate licence exists at `src/memit/LICENSE` (vendored MEMIT) — do not conflate* |
| WikiBigEdit | `raw.githubusercontent.com/ExplainableML/WikiBigEdit/main/LICENSE` — **[F]** `Copyright 2025 Lukas Thede` then an Apache-2.0 short header. **The inverted ordering is why the GitHub API reports NOASSERTION** |
| FreshQA | `raw.githubusercontent.com/freshllms/freshqa/main/LICENSE` — **[F] Apache-2.0**, *stock text, copyright line uninstantiated*. The LICENSE covers the repo; **the QA data lives in Google Sheets with no licence attached** |
| TAQA | `raw.githubusercontent.com/yizhongw/llm-temporal-alignment/main/LICENSE` — **[F] Apache-2.0**, *copyright line uninstantiated*; **the paper claims CC BY 4.0 — record both.** Data in-repo (`data/{train,dev,test}.jsonl`) |
| HyTE / Wikidata12k | `raw.githubusercontent.com/malllabiisc/HyTE/master/LICENSE` — **[F] Apache-2.0**, copyright line left literally as `Copyright [yyyy] [name of copyright owner]`. **Covers CODE only**; the data is a Google Drive zip with no licence inside. Mirror: `raw.githubusercontent.com/soledad921/ATISE/master/LICENSE` — **[F] MIT, © 2020 Chengjin Xu** |
| "When Facts Expire" | `zenodo.org/api/records/15680977` → `"license": {"id": "cc-by-4.0"}`, `rights: null`, published 2025-06-17. **[F]** (Zenodo licence record) |

**Three Apache-2.0 files in this list — FreshQA, TAQA, HyTE — ship stock text
with the copyright line never filled in.** There is **no named rightsholder to
cite** for those three. Record that as a fact; do not invent one.


---

## 4. BUILD-IT — rung 1: the MemoryCode **as-of re-cut**, $0, on material we already hold

**This is the cheapest instrument in the document that breaks recency
identification, and it needs no new data, no new licence, and no paid call.**

### 4.1 The construction

Today's probe (`load_memorycode`, `:163-179`): for a key declared at sessions
j₁ < … < j_m, **gold = j_m**, distractors = j₁…j_{m−1}. `max(observed_at)`
computes that.

The re-cut: **ingest exactly the same corpus, unchanged**, and ask the question
**as of an earlier session**. For each r ∈ 1…m−1 emit a probe with

```
valid_at        = observed_at(j_r) + ε        # ε < 1 minute; observed_at is EPOCH + index minutes
gold_unit_ids   = [session j_r]               # the declaration in force AT j_r
distractor_ids  = every other declaring session, INCLUDING j_{r+1} … j_m
```

**The whole corpus is still ingested**, so the later declarations are present in
the store and in the pool. This is the point: `max(observed_at)` now returns
j_m, which is a **distractor**. The A-recency control cannot answer an as-of
question at all — it has no closed generation to exclude and no remainder to
return. The bitemporal arm can, and that is precisely the machinery under test.

### 4.2 Why this is not a rigged control

The obvious objection: "just give the control a `≤ t` filter." That filter **is**
valid-time resolution. Adding it to the control means implementing the thing
being measured. The honest arm ladder is therefore three, not two:

| arm | rule | what it isolates |
|---|---|---|
| **A-recency** (`MEMPHANT_A_RECENCY_CONTROL=1`) | `max(observed_at)` per key, pure append, no generation ever closed (`crates/memphant-core/src/lib.rs:12115-12124`) | the trivial baseline, now provably wrong on every probe |
| **A-recency + as-of truncation** | `max(observed_at ≤ valid_at)` | **the real baseline.** A 20-line read-side rule. If the bitemporal arm cannot beat *this*, the edifice buys nothing that a `WHERE observed_at <= $t ORDER BY observed_at DESC LIMIT 1` does not |
| **bitemporal** | valid-time interval containment (`valid_for_query`, `lib.rs:10163-10167`) | remainder rectangles, closed generations, supersession edges |

**Arm 2 is the arm that matters and it did not exist before this document.**
A-recency-with-truncation is to the as-of cut what A-recency was to the current
cut: the strongest trivial alternative the instrument admits. Preregister the
primary comparison as **bitemporal − (A-recency + as-of truncation)**, and report
bitemporal − A-recency as a secondary, clearly labelled as a floor.

### 4.3 Why wrong retirement becomes expensive — the structural argument

Under the current cut, a declaration j_r with r < m is a distractor for the only
probe that mentions it, so retiring it costs nothing: **16 of 309 edges at
τ = 0.25 touch a gold (5.2%)** (`b1` §9.9).

Under the as-of cut, **every** declaration j₁…j_{m−1} is the **gold** of its own
probe. There is no declaration that is a distractor for every probe. Retiring a
prior therefore damages a gold unless the store keeps the historical rectangle
and the recall path returns it for the earlier `valid_at`.

**Stated as a preregistered gate, not a claim:** recompute the τ = 0.25 edge
census against the as-of gold set before scoring. **If the gold-touching share of
retirement edges is not ≥ 90%, the re-cut has not achieved property (d) and the
instrument must not carry the decision.** The construction predicts ~100%; that
prediction is falsifiable and must be checked, because the edge set is produced
by the extractor, not by the corpus.

### 4.4 The machinery is already in the tree — verified here, not assumed

| piece | evidence |
|---|---|
| `valid_at` is a live recall filter, not a stub | `RecallRequest.valid_at` (`memphant-types/src/lib.rs:480`) → `lib.rs:7012` → `valid_for_query` (`lib.rs:10163-10167`): `valid_from <= valid_at` and `valid_at < valid_to` |
| a request that sets it is flagged | `valid_time_override_requested` (`lib.rs:11274`) — a free liveness assertion |
| the harness already threads it | `gate_run_memphant.py:477,491-492,1106`; `gate_common.py:942-946,1026-1027,1071,1109` |
| supersession preserves history for an **unbounded** correction | `correction_rectangles` (`lib.rs:1064-1167`): `current_correction` ⇒ `start = now`; `start_lt(None, Some(now)) == true` (`lib.rs:1221-1227`) ⇒ **a remainder `[old.valid_from, now)` IS minted**. History is not destroyed |
| explicit intervals are available on the write path | `RetainUnitPayload.valid_from` / `.valid_to` (`memphant-types/src/lib.rs:1751-1754`), already used by `ingest_group_structured` / `ingest_group_derived` |

**Two build decisions this forces, and they must be made before the run:**

1. **Unbounded vs bounded ingest.** Unbounded (today's `RetainEpisodePayload`,
   which has **no** `valid_from` — `memphant-types/src/lib.rs:1742-1745`) puts the
   valid timeline on **wall-clock ingest order**, not the synthetic session
   timeline, so a probe's `valid_at` is not knowable a priori. **Use the bounded
   path**: ingest each session as a unit with `valid_from = observed_at(index)`,
   which puts valid-time on the session axis and makes `valid_at` computable from
   the loader alone.
2. **Bounded ingest changes the write path.** `bounded == true` skips the
   `existing_indices.truncate(1)` at `lib.rs:12492-12494` and routes through
   `interval_intersection`. That is a behaviour change, not a no-op. **$0 gate:
   run 20 groups and assert (i) non-zero `Supersedes` edges, (ii) non-zero
   remainder units, (iii) `remainders_recalled > 0` for at least one as-of probe.**
   Every prior lane on this corpus has reported `remainders_recalled: 0`
   (`2026-07-31-preference-writepath.md`); an as-of run that still reports 0 has
   measured nothing and must be voided, exactly as Arm F was.

### 4.5 What it cannot do

**MemoryCode contains zero re-assertion** (§1.1, measured). The as-of re-cut
delivers regimes **(c)** and **(d)**. It cannot deliver **(a)** or **(b)** at any
price, and no amount of re-cutting will change that. Those need a different
corpus.

### 4.6 Cost

| item | cost |
|---|---|
| adapter loader variant (`--instrument memorycode-asof`) | ~60 lines |
| bounded-ingest path for the memorycode arm | reuse `ingest_group_structured` |
| `valid_at` on the probe → recall call | ~5 lines; the field exists end to end |
| the third arm (A-recency + as-of truncation) | a read-side rule in the harness, not in the substrate |
| compute | 3 arms × ~1 h ingest+recall on the existing scratch-DB harness |
| **paid model calls** | **0** — MemoryCode's grade is deterministic regex; no reader, no judge |

---

## 4b. BUILD-IT — the coding instrument: MDN `browser-compat-data`

**If the answer is "build", this is what to build.** The plan already records
that the coding-cross-session-memory slot is empty in both directions:
sequential coding benchmarks have task pairing but **no "what should have been
remembered" annotation**, and memory benchmarks that have such annotations
contain **zero coding tasks** (confirmed again here from a second direction —
§3.3). BCD closes exactly that: it is an externally-authored, CC0,
machine-readable annotation of **which fact is in force in which scope**, over
code.

### 4b.1 Source of raw material — licence-clean, verified

**`mdn/browser-compat-data`**, one 19 MB `data.json`.
**[F] CC0-1.0**, fetched in this worktree; the GitHub API object agrees; **no
attribution obligation, no card-vs-file contradiction, no SDK-terms ambiguity.**
This is the only source in the survey with a clean licence on *both* the code
and the data.

### 4b.2 The annotation that makes wrong retirement expensive

BCD's support statements are `(feature, runtime, version_added,
version_removed, flags, prefix, alternative_name, partial_implementation,
status.deprecated)`. Counted from `data.json` v8.0.8:

| quantity | value |
|---|---:|
| compat features | 20,243 |
| support statements | 284,845 |
| **statements with BOTH `version_added` and `version_removed`** (bounded validity) | **7,454** |
| features carrying `status.deprecated` | 1,152 |
| **features with a strict removed-then-re-added arc** (regime **a**) | **325** |
| **browser-pairs exhibiting that arc** | **704** |
| runtimes covered | 17 |

**The cost of wrong retirement is executable, which no other candidate offers.**
Retire a still-live fact → the agent emits a needless polyfill or refuses.
Keep a retired fact → the generated code throws at runtime. **Both directions
are scoreable by running the code**, so `(d)` is not a proxy metric someone
invented; it is a program that fails.

### 4b.3 Why it is immune to our specific defect

**BCD has no timestamp axis at all.** Currency is keyed to **scope** — which
runtime, which version. `max(observed_at)` is not a weak baseline here; **it is
undefined**, and any timestamp we attach is one we manufactured.

The canonical probe, from the data: `AbortSignal.timeout()` is live in **Node
16.14**, **absent in 17.0** (the 17.x line branched before the backport), and
**live again from 17.3**. For a Node-17.0 query the newest assertion is the
**wrong** answer, and the right one is recoverable only from branch context.
The Edge (62 pairs) and Opera (128 pairs) arcs give the same shape via the
EdgeHTML→Chromium and Presto→Blink engine swaps; the 70 Node.js pairs are the
purest because the branch semantics are explicit.

### 4b.4 The two gates that must be paid before anything is built

1. **The naive arc query has ~75% false positives.** A first pass on
   "`version_removed` followed by a later `version_added`" returns **1,279**
   features; most are `partial_implementation` upgrades where the "removal"
   version equals the full-support "added" version (`AbortController` in Safari
   12.1 is the canonical trap). **Exclude `flags`, `prefix`,
   `alternative_name` and `partial_implementation`, and require a strict version
   gap.** That is what takes 1,279 → the defensible **325**. Any build that
   reports a number between those two has not applied the filter.
2. **BCD is not a memory corpus — it has no sessions.** The session stream must
   be synthesised, and that is the honest weakness: we would be authoring the
   *episodes*. **What we would NOT be authoring is the gold** — which fact is in
   force in which scope is BCD's annotation, CC0, externally maintained, and
   revision-pinnable. That is a strictly better evidence class than every coding
   bank we currently own, all of which are self-mined gold. **State this
   distinction in any publication; do not let it be blurred.**

### 4b.5 Breadth, and the pairing that fixes it

BCD is web-platform-centric — of the 704 arcs, **css 289 · api 269 ·
javascript 93**. For server-side breadth pair it with AOSP
`api-versions.xml` as a bounded-validity workhorse: **89,058 elements, 3,460
`deprecated=`, 1,004 `removed=`, 494 full since→deprecated→removed triples, and
66 genuine un-deprecations on the API 34→36 diff.** **Licence caveat, and it is
sharp:** the AOSP `NOTICE` is **[F] Apache-2.0**, but the repository root
`LICENSE` **404s** and the GitHub API reports `NOASSERTION`/"Other", and **the
copy shipped inside an installed Android SDK is governed by the Android SDK
Terms, not Apache-2.0.** If AOSP data is used it must be **regenerated from AOSP
source**, never lifted from a local SDK install.

### 4b.6 Size needed for a 7pt decision, computed

From `scripts/instrument_power.py`, `D_MIN = 0.07`, two-sided exact McNemar,
80% power: required n is **340 at ψ = 0.20**, **505 at ψ = 0.30**, **665 at
ψ = 0.40**. BCD's **704 browser-pair arcs** clear the ψ = 0.40 bar on the
re-assertion construct alone, and **7,454 bounded-validity statements** clear
every bar by an order of magnitude. **The instrument is not power-limited; it is
build-limited.**

### 4b.7 Cost

| item | cost |
|---|---|
| data acquisition | **$0** — one 19 MB JSON, no clone, no scrape, CC0 |
| arc extraction + the §4b.4 filter | ~1 day |
| session synthesis + probe generation | ~3–4 days |
| executable scoring harness (Node) | ~2 days |
| **paid model calls to reach a retrieval decision** | **0** |
| paid model calls if the reader/agent endpoint is added | **not costed here — a separate authorization**, and it must be gated on the retrieval result exactly as Arm 1 gates Arm 5 |

---

## 4c. The other candidate coding-currency sources, and why they rank lower

| source | construct | size (counted) | licence | why not first |
|---|---|---|---|---|
| **GitHub Advisory Database** | (b) publisher authority, (d) | **34,076 advisories, exactly 891 `withdrawn`** (full GraphQL pagination, 341 pages) | **[F] CC-BY-4.0** (`LICENSE.md`, fetched here); [A] agrees | Withdrawal is real non-recency retirement, but re-publication is rare and unlabelled; the repo is 3.5 GB (use the API) |
| **.NET SYSLIB obsoletions** | (b), **strongest documented (d)** | **65 SYSLIB0001–0065 + 2 EXTOBS = 67** — SYSLIB0002/0011 are compile **errors**, not warnings | **[F] CC-BY-4.0** docs + **[F] MIT** `LICENSE-CODE` (Microsoft) | n=67. Beautiful construct, unpowerable alone |
| **ESLint rule metadata** | (b), (c) | 292 rules · **93 deprecated · 91 with both `deprecatedSince` and `availableUntil`** · 20 removed · `replacedBy` on all | **[F] MIT, "Copyright OpenJS Foundation and other contributors"** (fetched here) | Explicit deprecation **with a replacement pointer** — the cleanest (b) signal in tooling. n≈93 |
| **Chromium / V8 / LLVM reland chains** | (a) at scale, (d) | `Reland`: **chromium 21,891** · v8 4,051 · llvm 2,620 (+2,588 `Reapply`, 1,284 `Recommit`) · **linux 2** (no convention) | **[F]** chromium BSD-3-Clause; llvm Apache-2.0-WITH-LLVM-exception (**[A] = NOASSERTION for both llvm and linux — the API is wrong**) | The best *narrative* source for expensive wrong retirement, and **no published dataset supplies the revert→reland link.** A mining project, not a download |
| **OpenJDK `ct.sym` / `jdeprscan`** | (a), (b) | 646 deprecated at rel-17 (425→646 over 7→17); 115 disappearances → **4 verified un-deprecations** (`String.stripIndent`, `translateEscapes`, `formatted`, `XMLInputFactory.newFactory`) | **[F] GPL-2.0 + `ASSEMBLY_EXCEPTION`** — **[A] says `GPL-2.0` and hides the Classpath Exception** | n=4 un-deprecations. A vivid anecdote, not an instrument |
| **Clippy `deprecated_lints.rs`** | (b) | **17 DEPRECATED + 76 RENAMED = 93**, each `#[clippy::version]`-tagged; **zero un-deprecations** | **[F] Apache-2.0 + MIT** dual — **[A] reports Apache-2.0 only, hiding the dual grant** | No (a); n=93 |
| **NVD REJECTED CVEs** | (a) weak, (d) | **372,463 total − 354,661 `noRejected` = 17,802 REJECTED** | ⚠️ **terms-only.** Attribution requested; **no public-domain statement and no redistribution grant found** ⇒ data licence **UNVERIFIED** | Large and tempting; the licence is the blocker |
| **IETF `rfc-index.xml`** | (b) authority | **9,819 `rfc-entry`** · 1,264 `obsoletes` / 1,383 `obsoleted-by` · 1,237 `updated-by` · 353 HISTORIC · 4,413 PROPOSED | ⚠️ **ABSENT for the index**: no copyright or terms line in `rfc-index.{txt,xml}`. TLP 5.0 **[F]** grants copying of *RFCs* and BSD for code components but **never mentions indexes, metadata or bibliographic data** | Perfect authority ordering, no licence covering the metadata, and not coding *behaviour* |
| **Academic revert datasets** (Yan EMSE-2019 125,241 commits/3,171 reverted; Shimagaki ICSME-2016 2,057; Wen EMSE-2022 500) | (a) partial | as listed | **ABSENT** — Yan ships no package at all; `yiu31802/icsme2016` and the USI-INF replication repo both 404 on LICENSE/COPYING with `[A] = null`. Wen's *paper* is CC-BY; **the grant does not extend to the repo** | **Legally unusable as published** |
| **apiwave / "RevertDataset"** | — | — | **ABSENT** — apiwave.com 302→404, no repo; **"RevertDataset" is a phantom name** with no artifact behind it | Do not plan against it |

---

## 5. Power — computed, not asserted

All figures from `scripts/instrument_power.py` (two-sided exact conditional
McNemar, α = 0.05, power integrated unconditionally over N_d ~ Binomial(n, ψ)).

**Required n for the program's preregistered 7pt decision (`D_MIN = 0.07`):**

| ψ | 0.10 | 0.15 | 0.189 | 0.20 | 0.25 | 0.30 | 0.35 | 0.40 | 0.50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **required n** | 166 | 260 | 320 | 340 | 425 | 505 | 585 | 665 | 825 |

**A point the trap section depends on:** an instrument that stops flattering
recency will have a **higher** ψ than MemoryCode's 0.26, because the arms will
disagree more. Higher ψ costs more n for a fixed δ — but only ~2× between
ψ = 0.10 and ψ = 0.40. **Discrimination is cheap in n and expensive in level.**

**MDE at 80% power for the as-of re-cut (n = 3,608):**

| effective n | ψ=0.20 | ψ=0.30 | ψ=0.40 | corresponds to |
|---:|---:|---:|---:|---|
| 3,608 | 2.11pp | 2.58pp | 2.98pp | no clustering (wrong — do not use) |
| 1,804 | 3.00pp | 3.66pp | 4.22pp | design effect 2 (the lock's own caveat) |
| 902 | 4.26pp | 5.20pp | 5.99pp | design effect 4 |
| 451 | 6.06pp | 7.38pp | 8.51pp | design effect 8 |

Probes cluster in **257 instances** at mean **14.04** per instance (max 71), so
DEFF = 1 + (m̄ − 1)·ICC is the binding quantity and it is unmeasured until the
run. **The 7pt decision survives up to DEFF ≈ 8 even at ψ = 0.30.** Score with a
cluster bootstrap over the 257 instances, as `memorycode.lock.json` already
prescribes, and report the realised DEFF beside the interval.

---

