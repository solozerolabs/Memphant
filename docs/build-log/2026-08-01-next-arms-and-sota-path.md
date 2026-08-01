# Next arms, the datasets we do not have, and the honest path to SOTA

**Date:** 2026-08-01 · **Branch:** `w2-arms` (base `main` @ `89cc22c4`) · **Spend:** $0.
**Method:** research and design only. No benchmark, ingest, server, or model call was run
in producing this document — the host was at loadavg ~108 on 12 cores with four
measurement lanes holding full-corpus ingests. Every arm below is **preregistered, not
run.** Every internal number is read from an artifact on disk (path given) or computed
with `scripts/instrument_power.py`. Every external number carries its source and a
verification status; **UNVERIFIED is used freely and is not an apology.**

Owner ask, verbatim: *"find more arms to test with ideal data sets and make sure we are
headed towards the SOTA level."*

**Answer in three lines.** (1) The best remaining arms are not more ranking levers — they
are the *controls we have never run*, and the published 2026 literature now says those
controls will probably beat us. (2) The dataset we most lack is a licence-clean corpus of
**repeated sessions on the same repository**; exactly one exists and we do not hold it.
(3) A public SOTA claim is reachable, costs ~$545, and the honest framing is not "we beat
Mem0" — it is "we closed X% of the oracle gap where the no-memory baseline is 19.68%."

---

## 0. The seven facts that determine everything below

**0.1 — The coding lane's ceiling and its exact shape.** Recomputed here from all 180 rows
of `~/.memphant-private/track-r-paraphrase/run-fusion/fusion_probe-provenance.json`:

| quantity | value |
|---|---:|
| goldens | 180 |
| gold present in the recall pool at all | **169** (0.939) |
| gold in fused top-10 (shipped arm) | **113** (0.628) |
| gold at fused rank **11–64** | **51** |
| gold at fused rank **> 64** | **5** |
| gold absent from pool | **11** |
| mean pool size | 124.2 (min 0, max 199) |

**A perfect reranker over the 64-deep head recovers at most 51 questions (+28.3pp); over
the full ~124-deep pool, 56 (+31.1pp).** Retrieval *into* the pool is nearly solved
(0.939); *ordering* is where 56 questions sit. Any arm claiming more than that is
mis-specified.

**0.2 — Fusion is at a local optimum and the obvious reranker is a powered negative.**
36 arms, none better (`2026-08-01-rerank-channel.md` §6.6). `bge-reranker-base` 113 → 79,
b=12/c=46, p=8.2e-06, power 0.996. That document's conclusion is this one's premise:
*"Closing the gap therefore needs a **new or better signal**, not a better combination of
the present two"* (§6.7).

**0.3 — Every ranking arm costs ~2 wall-clock hours, because there is no offline replay
fixture.** Measured (`z1-ladder-power.json`, `w02-trunk-arms.json`, `timings_seconds`):
ingest 937–1,083 s + worker compile **4,727–5,472 s** + recall 673–719 s = **~1.8–2.0 h
per arm** on 64,056 events. The banked provenance carries `channel_table` per question
(unit id, per-channel rank and score, `is_gold`, `fused_rank`) but **not candidate
bodies** — verified: `fusion_probe-evidence.jsonl` carries only the 10 packed bodies. So
no reranker, embedder, or query transform can be replayed offline. This is the binding
constraint on program throughput. It is fixable once. That is Arm 0.

**0.4 — The instrument's true MDE is 9.4pp, not 6.7pp.** The register lists Track R at
6.73pt; that is the *original* bank's ψ (b=15/c=3 ⇒ ψ=0.10). The **paraphrase** bank's
realized discordance on the decisive contrast is b=29/c=5 ⇒ n_d=34, **ψ = 0.189**:

| n | ψ | MDE @80% |
|---:|---:|---:|
| 180 | **0.189** | **9.38pp** |
| 180 | 0.25 | 10.71pp |
| 180 | 0.30 | 11.72pp |
| 1,063 (MemoryCode) | 0.26 | 4.46pp |
| 376 (SWE-CB Related) | 0.10 / 0.20 | 4.72 / 6.64pp |

Every arm below is preregistered at **MDE ≈ 9.4pp on n=180** against a 28–31pp ceiling.
Adequate. Where an arm cannot clear 9.4pp on argument, it is not proposed.

**0.5 — NEW EXTERNAL EVIDENCE: off-the-shelf reranking is net-negative on code, and our
result is the published norm rather than an outlier.** CoREB (contamination-limited code
retrieval benchmark, `hq-bench.github.io/coreb-page/`, CC-BY-4.0, live and versioned):
**4 of 5 off-the-shelf rerankers were net-negative on every code task** at top-128.
Jina-v2 −8.3 / −22.4 / −8.8; Jina-v3 −2.2 / −5.0 / −0.1; Qwen3-Reranker-0.6B −0.6 / −8.2
/ −2.3; Qwen3-Reranker-4B −0.1 / −3.2 / +3.3 ΔnDCG@10 (text2code / code2text /
code2code). **Only the code-fine-tuned CoREB-Reranker was net-positive.** No hosted
reranker (Voyage `rerank-2.5`, Cohere `rerank-v4.0-pro`) publishes *any* code number.
**This demotes reranking from the top of my ranking to a single gated diagnostic.**

**0.6 — NEW EXTERNAL EVIDENCE: the controls we never ran are the ones that beat memory
systems.** Three independent 2026 results, all pointing the same way:

| source | finding |
|---|---|
| **SWE-Explore**, arXiv:2606.07297 | HitFile@5 on issue localisation: **BM25 0.079 · TF-IDF 0.140 · Potion dense-RAG 0.088** vs **Claude Code 0.667 · AweAgent 0.682 · Codex 0.649.** An agent doing `grep`+`read` is **~7.6× better than dense RAG** and **~8.4× better than BM25.** |
| **MemDelta**, arXiv:2606.29914 | "Verbatim RAG matches full-context GPT-4o-mini (47.2 vs 49.8, p=0.34). **Agent self-memory 42% vs 47% for basic retrieval.** Mem0 matches cloud RAG (72.7 vs 73.9, p=1.0) **at 50× the cost.** Swapping the embedding model alone moves accuracy **+6.2pp (p=0.004)**." |
| **CL-Bench**, arXiv:2606.05661 | **Naive in-context learning beats dedicated memory systems.** ICL+Sonnet 4.6 25.4% normalized gain (#1) vs Mem0+GPT-5.4 20.2% (#4). |

Plan of record §2.1 already conceded *"BM25 is not a control — it is near-random on our
real task… Beating it by 0.37 is beating a floor"* — and then the program kept using it
as the control. The external literature has now closed that argument. **The controls move
to the top of the ranking.**

**0.7 — NEW EXTERNAL EVIDENCE: zero-shot embedders collapse on the task we actually do.**
CORE-Bench (arXiv:2606.11864, rev 2026-07-13, 180k queries, 106k context labels):
Qwen3-8B **collapses 71.7 → 20.3 nDCG@10** going from L1 code search to L2 issue→edit
localisation (−72%). In-domain SFT recovers most of it (+12.5 to +15.8 nDCG). This is the
same collapse plan §2.1 already cites. It lowers the prior on a zero-shot embedder swap
and raises it on in-domain adaptation — but in-domain SFT is out of budget, so Arm 2 stays
a screen, not a bet.

---

# PART 1 — Candidate arms, ranked by information per dollar

Ranked by information per dollar, and — because on this host CPU is scarcer than money —
CPU is priced explicitly beside dollars. **No arm appears without the $0 gate that
precedes it.**

| # | arm | endpoint | instrument | $ | CPU | MDE | ceiling | can falsify |
|---|---|---|---|---:|---:|---:|---:|---|
| **0** | **Pool-replay fixture** (enabler) | n/a | Track R paraphrase | **$0** | ~2 h once | n/a | n/a | — |
| **1** | **Agentic-search control (`grep` in a loop)** | packed hits@10 + reader | Track R paraphrase n=180 | ~$15–40 | ~0 | 9.4pp | n/a | **"MemPhant retrieval has value"** |
| **2** | **No-memory dense-RAG control** | fused hits@10 | Track R paraphrase n=180 | ~$0–3 | ~0 | 9.4pp | n/a | **"the substrate beats a 60-line script"** |
| **3** | Code-specialised embedder screen | fused hits@10 | Track R paraphrase n=180 | ~$0–7 | ~0–2 h | 9.4pp | +56 | "the embedder is not the bottleneck" |
| **4** | Agent-authored keys at write time | LSW / misapplication | MemoryCode n=1063 | ~$1–3 | low | **4.5pp** | 0.5795 | "keys cannot be produced" |
| **5** | **SWE-ContextBench `Related` n=376** | Resolved (%) | **public** | ~$545 | high | 4.2–8.1pp | 80pp headroom | the public claim itself |
| **6** | Late-interaction retrieval (LateOn-Code) | fused hits@10 | Track R paraphrase n=180 | **$0** | ~2 h | 9.4pp | +56 | "single-vector is enough" |
| **7** | One gated reranker diagnostic | fused order | Track R paraphrase n=180 | ~$0.5–3 | ~0 | 9.4pp | +51 | "reranking is dead on code" |

Arms 1, 2, 3, 6, 7 all consume Arm 0's fixture. Run them as one batch after it lands.

---

## Arm 0 — the pool-replay fixture. Before anything else.

|  |  |
|---|---|
| **Not a hypothesis** | Every ranking arm the program can propose is blocked behind a 2-hour full-corpus ingest, purely because candidate bodies are not banked. |
| **Mechanism** | `scripts/code_lane_run_memphant.py` already writes `channel_table` per question into the provenance JSON. Extend the writer to emit, per question, **the body of every pool candidate** to a gitignored sidecar. Pool depth is `DEFAULT_RECALL_POOL_DEPTH = 64` (`crates/memphant-core/src/lib.rs:522`); observed pools reach 199, so dump what the pool holds, not the constant. |
| **Deliverable** | 180 questions × ~124 candidates (~22k bodies, est. ~40 MB), gold-labelled, carrying the shipped fused order, bound by sha256 to the corpus and golden hashes already in `track_r_repo_memory_golden.lock.json` and stamped with git HEAD + binary hash per the standing lineage rule. |
| **Correctness gate** | Reproduce the banked `fused_hits_at_10 = 113` from the fixture alone, question by question. If it does not reproduce exactly, the fixture is void and nothing downstream may run. |
| **Cost** | $0. One full-corpus run, ~2 h wall. **Run it when the four measurement lanes are idle — not now.** |
| **$0 gate that could make it worthless** | (a) Are bodies already reachable? **Checked: no.** `fusion_probe-evidence.jsonl` carries only the 10 packed bodies per question. (b) **Unit ids are DB-minted per run**, so a fixture built from run A cannot be joined by id to run B. The fixture must therefore key on *bodies*, and each arm must re-derive its own order from bodies. If that join cannot be made deterministic, the fixture is worthless and arms 1–3, 6, 7 revert to ~2 h each. **Settle the join at $0 on 5 questions before the 2-hour run.** |
| **Why first** | It converts five arms from ~2 h each to minutes each — ~8 h of a contended 12-core host recovered for a 2 h investment, and it makes the *next twenty* ranking arms nearly free. On a host where CPU is the scarce resource, this is the highest information-per-unit-scarcity item in the document. |

---

## Arm 1 — the agentic-search control. The arm most likely to end a workstream.

**This is the falsification arm. If I could run exactly one thing, it is this.**

|  |  |
|---|---|
| **The belief it attacks** | That MemPhant's retrieval is worth having at all for a coding agent. Plan §D4 says a harness-native comparison "has never been done" and that "if Claude Code's own memory matches us on retrieval, the niche is governance-only." |
| **Why now, and not as a nice-to-have** | SWE-Explore (arXiv:2606.07297) measured exactly this contest on 848 issues over 203 repos: HitFile@5 **BM25 0.079, dense-RAG 0.088, Claude Code 0.667, Codex 0.649**. An agent doing `grep` + `read` in a loop is **~7.6× better than dense retrieval** on issue localisation. Independently, ContextBench (arXiv:2602.05892) file-level context F1 ranks **mini-SWE-agent (plain bash) 0.634 > SWE-agent 0.544 > OpenHands 0.463 > Prometheus (graph retrieval) 0.403 > Agentless (embedding retrieval) 0.390**, and concludes *"sophisticated agent scaffolding does not necessarily improve context retrieval performance."* **Every embedding-based retrieval system in that table loses to plain bash.** We have never run this control and we are an embedding-based retrieval system. |
| **The arm** | A no-substrate agentic control: give a model the 180 questions and **tool access to the raw corpus** (`grep`, `read_file`, `ls` over the 64,055 trajectory events on disk — no MemPhant, no index, no embeddings), capped at a fixed tool-call budget. Score the same provenance-span hit metric as every other arm, plus reader answer accuracy at the Phase 3 pin. |
| **Mechanism in our code** | Harness-side only. `scripts/code_lane_run_deterministic.py:100-140` already establishes the no-substrate control shape (`event_documents`, scoped, identical `retrieval_query` from `code_lane_run_memphant.py:264-279`, same evidence rows). The agentic control is a sibling that replaces `bm25_search` with a bounded tool loop. Reader/judge/packing reuse `scripts/code_lane_reader_prepare.py`, which already refuses to run unless every arm passes through **one** `gate_common.pack_evidence` at one k and one budget — the equalization that makes this comparison legitimate rather than the void 0.506-vs-0.806 shape. |
| **Endpoint / control** | Primary: **packed hits@10**, paired vs MemPhant's 106/180 at trunk. Secondary (preregistered as secondary): reader answer accuracy, `claude-opus-5`, judge `claude-opus-5`, `rag-supported-v1` — the pins already fixed in `2026-08-01-phase3-coding-reader-qa.md` §1. |
| **Expected effect, and what each direction means** | If MemPhant wins by ≥9.4pp: we beat the production alternative on its own material, and that is the first sentence a buyer cares about. If it is a wash: MemPhant is a **latency and cost** play over `grep`, not an accuracy play — defensible, but a different product. **If `grep` wins**, retrieval is not our contribution and the honest product is governance: supersession, forgetting, provenance, the file plane. The plan already contains that fallback; this arm decides whether we take it. |
| **Cost** | Agentic loops are the expensive control. ~180 questions × ~8–15 tool-calling turns at the `claude-opus-5` pin ($5/M prompt, $25/M completion, pinned via OpenRouter `provider.max_price` per `2026-08-01-phase3-coding-reader-qa.md` §1) ⇒ **~$15–40**, plus ~$10–20 if the reader bracket is run. Cap it with a hard tool-call budget and a per-question token ceiling. |
| **$0 gates — three, all mandatory** | (a) **Budget symmetry.** An unbounded agent will always beat a bounded retriever; SWE-Explore explicitly warns its own ground truth is *"structurally biased toward agentic explorers, and compute budgets differ by orders of magnitude."* Fix the tool-call budget and the token ceiling **before** the run, and report MemPhant's own token cost beside it. Without this the arm produces a number nobody should believe. (b) **The gold is a provenance span against a source event** (`scripts/track_r_retrieval_arm_compare.py:85-87` requires exactly one span per golden). A `grep` control returns file/line regions, not events — settle the span↔region scoring rule at $0 on 10 questions or the arm measures a mapping artifact. (c) **A no-evidence arm** (ONCU condition, plan §B3) on 20 questions: if the model answers correctly with no corpus access, those questions are contaminated and must be excluded from both arms. |
| **Why #1** | §2 of the plan lists ten things this program was recently wrong about. The cheapest way to find the eleventh is to run the control we have been avoiding, and the external literature now predicts we lose it. An arm that can end a workstream is worth more than an arm that can only confirm us. |

---

## Arm 2 — the no-memory dense-RAG control

|  |  |
|---|---|
| **The belief it attacks** | Same family as Arm 1, one rung cheaper and sharper: that the **substrate** contributes retrieval value over the same vectors used naively. MemDelta (arXiv:2606.29914) reports **verbatim RAG matching full-context**, **agent self-memory 42% vs 47% for basic retrieval**, and **Mem0 matching cloud RAG at 50× the cost**. Nobody has run that comparison against us. |
| **The arm** | Embed the raw corpus events with **the same embedder MemPhant uses**, cosine top-k, no memory unit, no compilation, no bitemporal state, no fusion, no packing. Second variant with the best embedder from Arm 3. Same 180 questions, same query string, same k. |
| **Mechanism** | ~60 lines against `scripts/code_lane_run_deterministic.py`'s existing interface, reusing Arm 0's fixture rather than re-embedding the corpus. |
| **Endpoint / control** | fused hits@10, MemPhant 113/180 vs dense-RAG top-10, paired exact McNemar, n=180. |
| **Cost** | **$0 if it reuses Arm 3's fixture vectors**; ≤$3 standalone. |
| **$0 gate** | The unit↔event mapping must be 1:1 or the comparison is void: MemPhant recalls *compiled units*, dense-RAG recalls *raw events*, and gold is defined against events. `z1-ladder-power.json` records `episodic_units: 64014` against `done_jobs: 64056` with `deduplicated_episodes: 42` — 1:1 modulo 42 dedups. **Verify that on the fixture at $0 before spending anything.** |
| **Interpretation** | A wash is not a null: it says the substrate's retrieval machinery is worth zero on this lane and the value must come from governance. That is a first-class finding and must be reported first if it occurs. |

---

## Arm 3 — the code-specialised embedder screen

|  |  |
|---|---|
| **Hypothesis** | The dense channel is `bge-small-en-v1.5` (384d) — a general-English retrieval model — embedding agent tool-call transcripts and diffs. A code-trained embedder is the "stronger retriever" §6.7 names. |
| **The uncomfortable fact** | `bge-small` is the **anchor** of the R0 bakeoff — the thing everything else was compared against — and it lost to nearly all of them on docs QA (`2026-07-11-r0-embedder-bakeoff.md`: small .133/.067 · modernbert .183/.100 · voyage-4 .217/.133 · voyage-context-4 .233/.117). R0 selected modernbert as "the R1 gate-flip embedder; shipped global default unchanged until R1 evidence." `build_embedder()` (`crates/memphant-runtime/src/lib.rs:366-372`) still resolves unset → bge-small. **The default never moved** — the same failure the dense-default flip documented on 2026-08-01: *"A measured win that does not move a default is not a win, it is a note."* And R0's code sub-bakeoff ran on 40 questions over 8 attempts of **one repo** (the bank now classed BROKEN/UNPOWERABLE) and deferred to "R4 with the full mined golden set." **R4 never happened. The embedder has never been varied on an adequately-powered coding instrument.** |
| **Mechanism — mostly zero code** | `embedder_from_id` (`crates/memphant-runtime/src/lib.rs:192-219`) already dispatches sixteen ids including **`voyage-code-3`**, `gemini-embedding-{001,2}`, `jina-v5-small`, `openai-text-embedding-3-small`, and locals `base`/`bge-m3`/`modernbert`/`gemma`/`qwen3`. The harness exposes it at `scripts/code_lane_run_memphant.py:745` (`--embed-model`), threaded into both server and worker env. **One addition is worth ~3 lines:** `jinaai/jina-embeddings-v2-base-code` — 161M, 8192 ctx, **Apache-2.0**, and **already shipped in fastembed**, so it is a new match arm in `fastembed_arm` (`:234-247`) and a local, $0, code-specialised embedder. |
| **The arms, priced from fetched vendor pages** | Corpus is 65.7 MB ≈ **16.4M tokens** (`~/.memphant-private/track-r/artifacts/corpus.jsonl`). |

| arm | licence | verified? | $/M tok | full-corpus $ | CPU | code evidence |
|---|---|---|---:|---:|---|---|
| `jina-embeddings-v2-base-code` (161M, in fastembed) | Apache-2.0 | **[V]** | — | **$0** | moderate | in-family; no 2026 number |
| `voyage-code-3` (already wired) | proprietary | **[V]** | **0.18** | **$2.95** | ~0 | CoIR 78.53 **[V]**; SWE-bench-Lite localisation **29.1** (CodeRAG) **[V]** |
| `gemini-embedding-2` (already wired) | proprietary | **[V]** | **0.20** (0.10 batch) | **$3.28 / $1.64** | ~0 | CoREB overall nDCG@10 **0.624**, rank 1 **[V]**; MTEB-Code **84.0 vs 74.66 — sources conflict, do not quote** |
| `BAAI/bge-code-v1` (2B) — **not wired** | Apache-2.0 | **[V]** | — | $0 | **heavy (2B, likely >2 h ceiling)** | **CoIR 81.77 (beats voyage-code-3)** and **SWE-bench-Lite localisation 67.4 vs voyage-code-002 29.1** **[V]** — the best licence-clean localisation evidence found |
| `openai-text-embedding-3-small` (already wired) | proprietary | ~0.02 **[U]** | ~0.02 | ~$0.33 | ~0 | Syndai-parity control |

|  |  |
|---|---|
| **Endpoint / control** | fused hits@10, paired vs the shipped `bm25code_dense` arm at **113/180**. |
| **Expected effect — with the prior lowered** | The p1 bench measured paid embedders at +0.084 to +0.098 R@5 retrieval-only over free bge on an adversarial chat pool; MemDelta measured an embedder swap alone moving accuracy **+6.2pp (p=0.004)**. Against that, **CORE-Bench's 71.7 → 20.3 collapse from code search to issue localisation** says zero-shot code embedders lose most of their advantage on precisely our task, and that the recoverable gain lives in **in-domain SFT** (+12.5 to +15.8) which is out of budget. Net: a real but not-assured ~9–16 question effect, right at the 9.4pp MDE. **This is a screen, not a bet, and it is priced like one.** |
| **The cost inversion worth stating in the authorization** | R0 measured bge-small at 34 min per 33k chunks and projected modernbert ~5.7 h and embeddinggemma ~5.0 h on that pool — both **RETIRED against a 2 h ceiling**. Scaled to 64k events those are 10–15 h of compile on a contended host. **On this machine the paid embedder is the cheap option and the free one is the expensive one.** `bge-code-v1` at 2B is the extreme case: best published localisation evidence, worst CPU cost, and it is not wired. |
| **$0 gate** | From Arm 0's fixture, embed only the **180 queries and ≤22k pool candidates** (≈$0.05–0.30) and recompute the vector channel's own rank of the gold. **If the candidate embedder does not improve the gold's dense rank on the 51 in-pool misses, it cannot help fusion and the full-corpus arm is dead.** This gate is the entire reason Arm 0 comes first. Second gate: `vec halfvec` has **no dimension typmod** (plan §4) — confirm a 1024d/2048d/3072d arm writes *and reads* before spending, because a dimension mismatch surfacing only at recall wastes the whole ingest. |
| **Not a §6 violation** | This changes the vectors, not the index. The exact scan is retained; no ANN. |

---

## Arm 4 — agent-authored keys at write time

|  |  |
|---|---|
| **Hypothesis** | Key production is the critical path (A4′). Deterministic rules top out at ~0.52 net recovery / 0.898 precision (`pre3_content_words`), inside the 63.4–68.3% band third-party work reports; a **mutation-time** LLM control-plane hook reaches 91.7–93.2% at ~$0.17/385 mutations, off the recall hot path (arXiv:2606.15903). **The caller that knows what the write is about is the coding agent itself.** |
| **Mechanism — and the part that surprises** | **No substrate change is required.** `RetainUnitPayload` (`crates/memphant-types/src/lib.rs:1745-1762`) already carries a **mandatory** `fact_key: String`, a mandatory `predicate`, and `target_unit_ids: Option<Vec<UnitId>>` (B1's rank-0-trust directed supersession). `RetainEpisodePayload` carries optional `subject`/`predicate` (`:321-325`). `RetainPayload` (`:1766-1770`) is exposed on REST (`crates/memphant-server/src/lib.rs:390`) **and over MCP** — the `retain` tool takes the same `RetainEpisodeHttpRequest` (`crates/memphant-mcp/src/lib.rs:314-357`). `derive_fact_key` (`crates/memphant-core/src/lib.rs:12761-12781`) only falls back to `{scope}:auto:{sha256}` when subject or predicate is absent. **A coding agent holding the MCP tool can author a key today. The missing piece is a prompt and a client, not a schema.** |
| **External corroboration for the shape** | AGENTbench (ETH, arXiv:2602.11988, MIT, 138 instances / 12 repos) measured **developer-written `AGENTS.md` at +4% and LLM-generated context at −3%** — i.e. *authored* context beats *derived* context. Probe-and-Refine (arXiv:2606.20512) measured tuned repo guidance at **33.0% vs static 28.3% vs none 25.5%** on SWE-bench Verified, p<0.001, with the gain coming from **coverage (+14.5pp reaching the right file)**, not patch quality. Both support caller-authored over extractor-derived. |
| **Endpoint / control** | LSW and misapplication on MemoryCode n=1063, against banked Arm A (0.3123 / 0.6717), with Arm P (0.5795 / 0.3406) as the **ceiling, not a comparator** — it is `decisional: false` because its key comes from the same `topic` field the gold does. |
| **Power** | n=1063 at ψ=0.26 ⇒ **MDE 4.46pp.** Best-powered instrument we own. Oracle gap is +0.267 LSW. |
| **Cost** | ~$0.50–3 for 8,147 episodes with cache replay, extrapolating the $0.17/385 figure, plus ~$1–3 to iterate the prompt. |
| **$0 gates** | (a) **Corpus shape.** Arm F already died here: `MEMPHANT_FACT_EXTRACTION=1` produced **2 semantic units from 8,147 episodes** because `extract_facts` matches first-person self-report while MemoryCode is second-person `Name: …` directives (plan §7.3). A caller-key hook is a different mechanism but the same question — answer it on 20 rows at $0. (b) **Liveness.** Assert non-zero `Supersedes` edges on a 20-row smoke: Arm A minted **0 edges of any kind** on 8,147 units, so the counter *is* the gate (plan §A5). |
| **Coordination** | `w1-keyprod` (`ed5fd6d7`) is measuring the deterministic key-production frontier and re-scores 0.535–0.899 under the regime supersession actually uses. **This arm is the caller-authored alternative, not a re-derivation.** Sequence after that lane reports. |

---

## Arm 5 — SWE-ContextBench `Related`, n=376. The only public claim available.

Fully preregistered on `w1-swecb` (`f99c8c4a`,
`docs/build-log/2026-08-01-swe-contextbench-retranche.md`); restated here for ranking only.
Endpoint **Resolved (%)** by the official grader; arms `no_memory` vs `memphant_memory`,
**both run by us**; n=376; **MDE 4.23–8.11pp** across ψ=0.08–0.30. Published no-memory
baseline **19.68%** (paper Table 3, `Claude Sonnet 4.5 / Claude Code`); oracle summary
23.40%. Cost **~$545** ($0 stage-0 adapter round trip → ~$40 ψ pilot → ~$504 measurement;
band $440–$780). **$0 gate: the stage-0 adapter contract round trip**, mandatory under
standing rule §5 — three adapters have failed at first contact, two after money was
authorized.

It ranks fifth on information per dollar and **first on strategic value**, because it is
the only instrument in the register that can back a public claim. See Part 3.

**Licence disagreement, recorded rather than smoothed.** Our own `w1-swecb` audit fetched
the HF tree and the GitHub tree (1,095 entries, `truncated: false`) and found **no LICENSE
file anywhere**, recording `license_source: RECORD_METADATA, verified: false`. An
independent sweep run for this document reported the same underlying artifact — README
YAML front matter `license: mit`, `cardData.license: "mit"` — but graded it *verified*.
**Our stricter reading stands.** A card field is a self-declared uploader metadata value,
not a licence artifact; the register's F3 finding exists precisely for this distinction.
Consequence unchanged: usable for local measurement and citation, **not
redistribution-clear.**

---

## Arm 6 — late-interaction retrieval (the one genuinely new lever)

|  |  |
|---|---|
| **Hypothesis** | Multi-vector late interaction preserves token-level lexical alignment — identifiers, stack traces, error strings — that a single 384-d vector averages away. That is exactly the signal plan §2.2 says our material carries. |
| **Why it is newly available** | The p1 bench tried this and **failed for an infrastructure reason, not a scientific one**: *"V6 ColBERT MaxSim — BLOCKED. Jina ColBERT v2 multi-vector endpoint hard-403'd (Cloudflare WAF) at benchmark volume after 13/24 questions."* As of 2026-02-11 there is a self-hostable, **Apache-2.0**, ONNX-shipping alternative: **`lightonai/LateOn-Code`** (149M, 128 dims/token) and **`LateOn-Code-edge`** (17M, 48 dims/token). |
| **The published evidence** | MTEB-Code v1 avg nDCG@10 **[V]**: LateOn-Code (149M, multi-vector) **74.12** vs GTE-ModernBERT (149M, single-vector) **71.66**; LateOn-Code-edge (17M) **66.64** beats granite-embedding-small-r2 (47M) 55.84 by 10.8 and comes within 2.1 of embeddinggemma-300m. **At matched parameter count, late interaction wins clearly.** It does *not* beat 0.5–0.6B single-vector models that are 3–4× larger — the honest framing is **parameter efficiency, not absolute SOTA.** |
| **Mechanism** | A new `EmbeddingProvider` variant is the wrong shape — `EmbeddingProvider` returns one vector. Measure it **offline against Arm 0's fixture first** (MaxSim over 180 queries × ~124 candidates is seconds of CPU), and only design the in-product seam if it wins. |
| **Endpoint / control** | fused hits@10, paired vs 113/180. |
| **Cost** | **$0** (Apache-2.0 weights, ONNX, local). ~2 h if it graduates to a full-corpus arm. |
| **$0 gate** | (a) **Index size.** No published index-size ratio for multi-vector on a code corpus exists (**UNVERIFIED**); the literature says "orders of magnitude" over single-vector, mitigated but not eliminated by PLAID/residual compression. Measure the ratio on the fixture before contemplating production. (b) **Context window.** LateOn-Code is doc 2048 / query 256 tokens; our bodies have median 1,466 chars, p90 4,087, max 8,560 (`2026-08-01-rerank-channel.md` §3.6) — so a meaningful fraction truncates. Quantify that on the fixture at $0, because the same caveat was *retracted* as an explanation for the bge failure (§7.4) and must not be assumed here either. (c) **All published code evidence is CoIR/MTEB-Code — short queries, short clean snippets. Nothing publishes a late-interaction number on tool-call JSON, diffs, or test output.** Prior, not prediction. |

---

## Arm 7 — one gated reranker diagnostic, and no more

|  |  |
|---|---|
| **Why this is now small** | Our `bge-reranker-base` result (113 → 79, p=8.2e-06, power 0.996) is **the published norm.** CoREB found 4 of 5 off-the-shelf rerankers net-negative on every code task; only the code-fine-tuned CoREB-Reranker was positive. So "a *different* reranker is an open question" narrows to a single named candidate. |
| **The candidate** | **`hq-bench/coreb-code-reranker`** (2026-05-06, **CC-BY-4.0**, Qwen3-Reranker-4B + LoRA) — the only 2026 code-fine-tuned reranker and the only net-positive one on CoREB. Offline replay over Arm 0's fixture, **diagnostic-only**. |
| **Endpoint** | fused order: does *any* reranker put more of the 51 rank-11–64 golds into the top 10? Paired vs 113/180, ceiling +51. |
| **Cost** | ~$0 self-hosted offline (4B on CPU is slow but this is 180 × 124 pairs once, not a served path), or ~$0.50 hosted at Voyage `rerank-2.5` **$0.05/M tokens [V]** on our ~9.9M rerank tokens. |
| **The gate that makes it diagnostic-only, decided in advance** | **No reranker has a published number supporting top-64 CPU rerank at p50 <200 ms on code-realistic (~512-token) documents.** Published CPU throughput, complete set: `ettin-reranker-17m` **267 pairs/s at 512-token docs ⇒ 239 ms for 64 pairs** (the *only* measured candidate anywhere near budget, and it has **zero code numbers**); ettin-32m 692 ms; ettin-68m 2.05 s; ettin-150m 4.6 s; `gte-reranker-modernbert-base` (149M, **CoIR 79.99 — best code quality per parameter found**) ≈4.6 s by the same analogue. Hosted: every third-party p50 measurement lands **250–620 ms** and **no vendor publishes an SLA**. Our HTTP-boundary acceptance figure is p50 32.59 ms / p95 37.18 ms (`docs/build-log/artifacts/c1-episodic/slo-bar1-http-provenance.json`) against a 200 ms SLO. **The size class that fits our budget (<35M) and the size class with any evidence of helping on code (149M–4B) are disjoint.** Therefore: preregister this arm as **diagnostic-only, deployment-blocked**, exactly as A1/Deep was. If it wins, the finding is "a code-tuned reranker can reach the 51" and the next question is distillation, not deployment. |
| **Second $0 gate** | From the fixture, plot the fused-rank distribution of the 51. If they are uniform through 11–64 rather than concentrated near the head, no shallow-window reranker reaches them and the window must be the full pool — which changes the latency arithmetic again. |

---

## Arms explicitly NOT proposed, with the reason

| not proposed | why |
|---|---|
| Any fusion-constant tuning | 36 arms, none better; K=60 correct. `2026-08-01-rerank-channel.md` §6.6. |
| More local cross-encoder reranking | Powered negative + CoREB's 4-of-5 net-negative. Arm 7 is the one remaining named candidate. |
| Hosted reranker **for deployment** | No vendor publishes a code number or an SLA; all third-party p50s are 250–620 ms against a 200 ms budget. Measurement only. |
| ANN / pgvector / HNSW | Three structural blockers; plan §4. Arm 3 changes vectors, not the index. |
| Hot/cold tiering | Does not exist, is not being built. Plan §4. |
| STATE-Bench | Plan §6: $2,254–$10,704. Nothing new. |
| Learned/RL memory management | Plan §6. Nothing new. |
| **Query transformation / HyDE / instruction-prefixing** | **Withdrawn from my own draft on the evidence.** arXiv:2605.08299 (2026-05-08, 6 CoIR sets × 5 encoders × 3 rewriters = 90 configs): **rewriting degraded retrieval in 56/90 configs (~62%)**, and *no* rewriting beat the baseline on NL-heavy text-to-code. CORE-Bench: *"Rewritten queries often fail to improve retrieval and can even reduce performance."* arXiv:2603.13301 supplies the mechanism — degradation co-occurs with **reduced lexical overlap** — which applies with extra force to stack traces and identifiers. Our own −0.139 R@5 instruction-prompting loss is unpublished but mechanistically expected (bge-small was trained with exactly one query convention). **This was §6.7's third named path and it should be struck from it.** |
| **Verbatim-vs-derived ablation** | I drafted this as a falsifier of *"verbatim is the memory"* and then found the belief **externally corroborated**: MemDelta reports verbatim RAG matching full-context; TACO (arXiv:2604.19572) names our exact failure mode — *"generic pruning drops critical strings, abstractive summarisation paraphrases them away"* — and arXiv:2607.13071 documents Claude Code compaction summaries recording a killed process's partial stdout as confirmed results, inherited downstream as ground truth. It also has a blocking $0 gate: gold is a **provenance span** (`track_r_retrieval_arm_compare.py:85-87`), and a derived summary has no span, so it can never score as a hit. **Lower value than when I drafted it; not proposed at this budget.** |
| Track U, `coding_events_golden`, LongMemEval-V2, `SWEContextBench_Related_Lite` (n=99) | Register stop conditions / underpowered. A win at n=99 would be exactly as meaningless as Table 4's. |

## Standing-rule addition this analysis earns

**Hold the embedder, the retriever, and the reader fixed when attributing any
memory-architecture delta.** MemDelta measured an **embedder swap alone moving accuracy
+6.2pp (p=0.004)** — larger than most architecture effects this program has chased. Arms 1
and 2 must therefore pin the embedder to the shipped one, and Arm 3 must not be confounded
with any packing or fusion change.

---

# PART 2 — Ideal datasets we do not have

Split as the brief requires: **(a) instruments that make a PUBLIC claim possible** — the
SOTA gap — and **(b) instruments that only improve internal decisions.**

Licence method legend: **[F]** an actual LICENSE file or Zenodo licence record was
fetched · **[C]** the HF `cardData.license` self-declared metadata field was fetched (a
claim by the uploader, **not** a licence artifact) · **[A]** GitHub API licence object ·
**ABSENT** the field was verified missing · **[U]** unverified.

## 2a. Instruments that could make a PUBLIC claim possible

| # | dataset | construct it unlocks that nothing we hold can | licence | size | acquire | run |
|---|---|---|---|---|---|---|
| **A1** | **`SALT-NLP/SWE-chat`** — hf.co/datasets/SALT-NLP/SWE-chat | **Repeated real agent sessions on the SAME repository.** 5,851 sessions across **205 repos**, 2.69M turns, 13,406 checkpoints, 14,459 commits, captured from Claude Code / Codex / Gemini CLI / Cursor / OpenCode / Copilot CLI on public repos. This is *the* construct MemPhant exists to serve and **we hold no instrument that measures it.** Every bank we own is one-shot-per-instance. | **ODC-BY [C]** | 5,851 sessions | **GATED** (contact info required); PII-redacted via Presidio+TruffleHog; **live takedown channel — treat local copies as revocable, retain session/turn ids** | $0 to ingest; golden mining is the cost. Labels present: `commits.is_agent_author`, `file_attribution`, `checkpoints.files_touched`, `session_success` |
| **A2** | **ChainSWE** — arXiv:2607.02606, hf.co/datasets/C-lister/ChainSWE | **Sequential dependent fixes in one repository with state carried forward, not reset.** 100 chains / 304 instances / 54 repos. The published result is the strongest public statement of the problem we solve: **resolve rate drops 68.4% → 20.6% by chain position 3**; per-position Oracle vs Sequential GPT-5.5 75.9→28.2, Opus-4.7 72.0→24.0, DeepSeek-V4-Pro 53.3→15.1; and *"conversation memory helps only GPT-5.5."* **A 48-point drop is enormous headroom and the paper says existing memory does not close it.** | **MIT [C]** (card field only — apply the SWE-CB standard, not the sweep's) | 304 instances | $0 | agentic, comparable to SWE-CB per-task cost; **n=304 gives MDE ~5.2–9.0pp at ψ=0.10–0.30** |
| **A3** | **SWE-Bench-CL** — github.com/thomasjoshi/agents-never-forget | Chronological per-repo task streams over SWE-bench Verified **with published forgetting / forward-transfer / backward-transfer metrics**. The only public instrument that scores *forgetting* on coding work — the construct ForgetEval measures only on our own self-authored adversarial bank. | **MIT [A]** — a real LICENSE file, "Copyright (c) 2025 Thomas" | 45 MB | $0 | agentic; **3-author student-scale project, last push 2025-05-18, no results table published — a citation here carries little weight** |
| **A4** | **ContextBench** — arXiv:2602.05892, contextbench.github.io | **Human-annotated gold context: 522k lines of minimal-necessary spans** `{file, start_line, end_line, content}` over 1,136 tasks / 66 repos / 8 languages. This is the ideal "what should have been retrieved" label and it does not exist anywhere else. It also hosts **the only live coding-context leaderboard** (DeepSeek-V4-Pro 57.5% pass@1 / 0.338 ctx-F1; Sonnet 4.5 53.0 / 0.344; GLM-5.1 51.4; GPT-5 47.2). | **BLOCKED.** Code Apache-2.0 **[A]**; **data licence VERIFIED ABSENT** — the HF card front matter has no `license:` key and the site footer reads *"© 2026 ContextBench Research Group · All Rights Reserved."* | 1,136 tasks | **$0 and an email.** Ask the authors for a data licence. Highest-value single email available. | leaderboard-comparable |
| **A5** | **TraceLab** — arXiv:2606.30560 | **~4,300 real Claude Code + Codex sessions, ~350k LLM steps, ~430k tool calls, dataset + pipeline released.** The largest verifiable public coding-trajectory corpus. Our Track R corpus is 495 *synthetic-rollout* attempts from SWE-rebench; this is real production agent behaviour. Would replace the corpus under our existing golden-mining pipeline. | **CC-BY-4.0 [U — claimed in paper, licence artifact not fetched]** | ~4,300 sessions | $0 | corpus swap; re-mine goldens (the pipeline exists: `scripts/track_r_mine.py`, `track_r_paraphrase_mine.py`) |
| **A6** | **CL-Bench Codebase Adaptation** — arXiv:2606.05661, continual-learning-bench.com | Cross-episode learning with a step-efficiency reward, on real repos (tablib, tenacity). Has a submission path and a published Table 1. Its headline is directly adversarial to us: **naive ICL 25.4% beats Mem0 20.2%.** | **Apache-2.0 [F]** | 19 bugfixes in the coding domain | $0 | **n=19 in the coding slice — UNPOWERABLE for us.** Citable, not measurable. |

**The (a) verdict.** Only **A2 (ChainSWE)** and **A1 (SWE-chat)** are both licence-plausible
and powered. A4 (ContextBench) is the best construct in existence and is
all-rights-reserved. A3 and A6 are too small or too weakly cited to carry a claim.
**Combined with Arm 5, the public-claim surface is exactly three instruments:
SWE-ContextBench `Related` (376), ChainSWE (304), and — if the gate is accepted —
SWE-chat.**

## 2b. Instruments that would only improve internal decisions

| dataset | what it improves | licence | note |
|---|---|---|---|
| `ByteDance-Seed/Multi-SWE-bench_trajs` | 6.27 GB of real leaderboard trajectories, **9 languages** — our corpus is Python-dominant | **CC0-1.0 [C]** | least-encumbered trajectory dump found; zero attribution burden |
| `yoonholee/terminalbench-trajectories` | **52,104 rows**, 109 agent×model combos, clean binary `reward` (39.6% pass) | **Apache-2.0 [C]** | third-party aggregation of the TB2 leaderboard, not first-party |
| `harborframework/terminal-bench-2-leaderboard` | **≥5 repeat trials per task** — the only *repeat-attempt* signal in the harness world | **Apache-2.0 [C]** | 121.6 GB |
| `SWE-ContextBench_Relationship` (**we already hold it**) | 376 explicit **base→related task pairings** — "which prior task should be recalled" | same as A5 above | already pinned in `swe_contextbench.lock.json`; underused |
| `DeepCommit-ai/SWE-Milestone-data` | sequential milestone DAG on the same repo, 98 graded milestones / 110 DAG edges | **MIT [C]** | no trajectories |
| AGENTbench (ETH) | developer-written `AGENTS.md` paired with PR tasks ≈ authored-context ground truth | **MIT [A]** | 138 instances — powers Arm 4's design, not a measurement |
| AIDev (Zenodo 16919272), DevGPT v10 (Zenodo 16392320) | 33,596 agentic PRs; 17,913 human↔ChatGPT prompts tied to repo artifacts | **CC-BY-4.0 [F]** (Zenodo licence record) | PR-granular / one-shot; continuity incidental |

## 2c. Verified traps — do not ingest, and the reason

| item | evidence |
|---|---|
| **`swe-bench/experiments`** | The richest trajectory dump in the SWE-bench world (predictions + execution logs + trajectories for every leaderboard entry). `api.github.com/repos/swe-bench/experiments` → `"license": null`; `/main/LICENSE` → **404**. **All rights reserved by default.** MIT on `SWE-bench`/`SWE-agent` covers **code only**. This will look like the jackpot in any search. |
| **SWE-Explore-Bench data** | Repo LICENSE is **MIT [F]** — but the HF dataset is **`cc-by-nc-nd-4.0`**: non-commercial *and* no-derivatives. An MIT badge over an NC-ND dataset. Our register already lists SWE-Explore as BROKEN for a different reason (0/848 usable rows); this is a second, independent reason. |
| **LoCoMo** | **CC BY-NC 4.0 [F]** — the single most-quoted vendor benchmark in the market is **not commercially usable**. And see Part 3: it is also 6.4% wrong. |
| **BEAM / BEAM-10M** | **CC BY-SA 4.0 [F]** — copyleft ShareAlike. |
| **MemBench** | README shows an **MIT badge**; `/repos/.../license` → 404, `/blob/main/LICENSE` → 404, API `license: null`. **VERIFIED ABSENT.** The exact badge-vs-file trap our prior audit hit. Data also lives on Google Drive / Baidu Pan. |
| `princeton-nlp/SWE-bench{,_Verified,_Lite,_Multimodal}` HF datasets | licence field **VERIFIED ABSENT** on all |
| SWE-Gym trajectory dumps, `OpenHands/CodeScout_*_Rollouts`, `togethercomputer/CoderForge-Preview` (413k traj / ~93 GB), `R2E-Gym/R2EGym-SFT-Trajectories`, `JetBrains-Research/EnvBench-trajectories`, LongCodeArena `lca-ci-builds-repair` | licence **ABSENT** |
| `jina-code-embeddings-*`, `SFR-Embedding-Code-*`, `SweRankEmbed-Small`, `jina-reranker-v2/v3/v3.5`, `jina-embeddings-v5-*` | **CC-BY-NC-4.0** — the entire Jina and Salesforce code-retrieval line is non-commercial. `SweRankEmbed-Small` is especially tempting (137M, SWE-bench-Lite Func@10 74.45) and especially unusable. |
| `nvidia/*-openhands-trajectories`, `nvidia/Open-SWE-Traces` | CC-BY-4.0 **[C]** but cards state the trajectories were **synthesized with Minimax-M2.5 / Qwen3.5-122B / Qwen3-Coder-480B** — model-generated, SFT-reward-filtered. **Not a neutral distribution of agent behaviour**, and the failure/recovery episodes a memory system most needs are systematically underrepresented. |
| KaVE/FeedBaG++ (11M IDE events) | not a licence: *"you agree to use them only for scientific purposes."* Research-only. |
| BlueJ Blackbox | **DEAD** — *"As of January 2026 the project has been stopped… existing access will be revoked in August 2026."* |
| JetBrains 151.9M-event / 800-dev IDE corpus; Meta REAP; SWE-Together (2606.29957, 11,260 real sessions); AMA-Bench (2602.22769); CORE-Bench's 106k context labels | **papers exist, data not released.** Do not plan against them. |

## 2d. Two free integrity fixes this audit surfaced

1. **LongMemEval-S's licence is fully verifiable and is not recorded as such.** Register
   finding F3 says `longmemeval_s.lock.json` "carries no license field at all" — that is
   **stale**; the lock now records `license: "mit"` with
   `license_source: "HuggingFace dataset card metadata (cardData.license)"`. It can be
   upgraded to the memorycode standard: an actual `LICENSE` file exists at
   `raw.githubusercontent.com/xiaowu0162/LongMemEval/main/LICENSE`, MIT, "Copyright (c)
   2024 Di Wu". **Pin the blob sha256.** $0, closes F3 for this lane.
2. **`SWEContextBench_Relationship` (376 base→related edges) is pinned and unused.** It is
   the only "which prior task should be recalled" label we own. Free.

## 2e. The gap, stated plainly

**We hold zero instruments that measure repeated work on the same repository.** Every
coding bank we own — Track R (495 attempts, one-shot each), `coding_events_golden` (8
attempts, one repo), SWE-ContextBench (task records, no trajectories) — is
one-shot-per-instance. The product's entire thesis is that memory across sessions on a
repo is worth something, and **no instrument in the register can express that
proposition.** Exactly one licence-plausible public source closes it (SWE-chat, 205 repos
with multiple sessions each), and acquiring it costs a contact form.

---

# PART 3 — The honest path to SOTA

## 3.1 What a credible SOTA claim for coding-agent memory would consist of in 2026

**The single most important finding of this research: there is no live public leaderboard
anywhere that scores cross-session memory for coding agents.** Verified by fetching. The
slot is empty — which is the opportunity and also the reason no claim is currently cheap
to make legible.

The landscape, fetch-verified 2026-08-01:

| what exists | status |
|---|---|
| Coding leaderboards with live numbers — SWE-bench Pro (top `deepreinforce-ai/Ornith-1.0-397B` **62.2**, `zai-org/GLM-5.2` **62.1**), SWE-rebench (operator-run, rolling; Fable 5 **64.5%**, Grok 4.5 63.8%, Opus 5 63.4%), Terminal-Bench 2.1 (Claude Code+Fable 5 **83.8% ±1.2**), ContextBench (DeepSeek-V4-Pro 57.5% pass@1) | **All live. None scores memory.** Single-shot, repo reset per task. |
| Memory benchmarks with leaderboards | **None.** LoCoMo, LongMemEval v1, MemBench, MemoryAgentBench, MemoryArena, Momento, BEAM, RECON, PM-Bench — **not one has a leaderboard**, and none contains coding tasks. |
| LongMemEval-V2 | Leaderboard **BUILT and EMPTY** — both tiers read "Leaderboard entries coming soon." Apache-2.0 **[V]**. Reference frontier only: RAG slice+notes 51.0% @0.2s · AgentRunbook-R 58.6% @26.9s · **Codex 69.9% @177.2s** · AgentRunbook-C 74.9% @108.3s. Scores **LAFS** = accuracy over log-scaled latency. **Site and arXiv abstract disagree on the numbers (abstract: 72.5/48.5/69.3)** — pin the split before quoting. Web-agent trajectories, not coding. |
| Neutral operator-run memory evaluation | **Does not exist.** The one candidate, AMB (`agentmemorybenchmark.ai`, `vectorize-io/agent-memory-benchmark`), is **built and operated by Vectorize, whose own product Hindsight is ranked #1 on it.** |

**So a credible 2026 claim has to be constructed, not entered.** Its four required parts:

1. **On a coding artifact.** SWE-ContextBench (real repos, cross-issue experience reuse),
   ChainSWE (sequential dependent fixes), or CL-Bench Codebase Adaptation. **Not LoCoMo.**
2. **Against the right baselines** — **no-context** and **naive ICL**, not other memory
   vendors. Two independent papers show memory systems losing to doing nothing.
3. **Against a measured oracle ceiling.** "We closed X% of the oracle gap" is falsifiable;
   "SOTA on LoCoMo" is not.
4. **Third-party-runnable.** Ship prediction artifacts; do not gate the headline behind a
   closed tier.

## 3.2 The specific numbers to beat

**SWE-ContextBench (arXiv:2602.08316 v3) Table 4 — the only published head-to-head of
real memory vendors on a real coding benchmark.** Claude Sonnet 4.5, 99 SWE-Bench-Lite
related tasks:

| setting | Resolved | cost |
|---|---:|---:|
| No-Context | **26.26%** | $0.79 |
| Oracle Context (raw) | 27.27% | $0.77 |
| **Oracle Summary** | **34.34%** | $0.85 |
| **Supermemory** | **30.30%** (F2P 55.95%) | $0.58 |
| OpenViking | 29.20% | $0.53 |
| **Mem0** | **24.24%** | $0.62 |
| LangMem | 73.34% retrieval match rate, **worse resolution** | — |

**Read cold: Mem0 scores below doing nothing.** The best deployed memory system leaves 4
points on the table against a perfect summary oracle. Table 3 (5 agents, full set) is
harsher: baselines 16–23%, oracle summaries buy only **+1.3 to +5.3pp**.

**Table 4 is not a measurement by our own floor.** n=99; detecting Supermemory's 4.04pp
margin has power **0.08–0.15**; required n is **510–1,485**. Supermemory 30/99 vs baseline
26/99 is a **four-instance difference.** We must not cite that ranking as a measurement
and must not try to beat it at n=99 — a win there would be exactly as meaningless as
theirs. **The n=376 `Related` split is the only defensible target, and its published
no-memory baseline is 19.68% with oracle summary 23.40%.**

**The vendor numbers everyone quotes are worthless.** All self-run, none coding:

| vendor | claim | run by |
|---|---|---|
| Mem0 | LoCoMo 92.5%, LongMemEval 94.4% | SELF; headline **requires Mem0 Cloud with "proprietary optimizations not available in the OSS SDK"** — unreproducible by construction |
| Zep/Graphiti | LongMemEval 63.8%; LoCoMo 75.14% (**revised down from its own 84%**) | SELF |
| Hindsight (Vectorize) | LoCoMo 89.61%, LongMemEval 91.4% | SELF, **and owns the leaderboard it is #1 on** |
| Memobase | LoCoMo 75.78% | SELF — **best repro hygiene: ships raw predictions** |
| Cognee | 0.93 on a 24-question HotPot subset | SELF, **admits "this is cognee's benchmark, not an independent study"** |
| Supermemory | LongMemEval_s 95% Recall@15 | SELF (third-party ran them at 30.30% resolved) |
| LangMem | — | **no primary source exists**; every quoted number traces to Mem0 using LangMem as a baseline |

**And the benchmark under all of it is broken.** Penfield Labs audit
(`dial481/locomo-audit`, 2026-04-04): **99 score-corrupting errors in 1,540 LoCoMo
questions (6.4%)**, 24 speaker-attribution errors, and a **62.81% judge false-positive
rate**. 56% of per-category system comparisons are indistinguishable from noise. The
75.78 → 92.5 vendor spread sits **inside the benchmark's own error bars.** Two Mem0
reproduction failures are on record and closed without a documented answer
(`mem0ai/mem0#3944`, `#2800` — an outside user got ~0.20 against a claimed 0.67–0.925).
**Every LoCoMo ranking is unfalsifiable.** The owner's standing rule that vendor self-run
claims do not count is, on this evidence, understated.

## 3.3 Where MemPhant actually stands

| instrument | MemPhant | comparator | verdict |
|---|---|---|---|
| SWE-ContextBench `Related` n=376 | **never run** | published no-memory **19.68%** | **unmeasured.** ~$545 away. |
| ChainSWE | never run | resolve **68.4% → 20.6%** by position 3 | unmeasured; the paper says existing memory does not close it |
| Track R paraphrase (private) | fused@10 **113/180**, packed@10 **106/180** | scoped BM25 **0.2556 r@10** | a win over a floor the literature calls near-random (BM25 HitFile@5 **0.079**) |
| Track R vs **any strong control** | **never run** | — | **Arms 1–2. This is the hole.** |
| MemoryCode preference lane n=1063 | LSW **0.3123**, misapplication **0.6717** | BM25 control **0.3198 / 0.6736** | **LOSES to a lexical baseline.** Oracle ceiling 0.5795. |
| Syndai docs gate n=60 | hit@10 **−0.133**, QA **−0.167** | Syndai's own stack | **LOSES to the incumbent** |
| ForgetEval-385 (self-authored) | 244 pass / 15 fail vs baseline 133 / 126; b=111, c=0, n=259 | our own baseline | a real, well-powered win — **on our own bank** |
| Answer accuracy, any lane | **never measured** | — | Phase 3 is the pending paid run |

**Plainly: we are far away, and the gap has a precise shape.** It is not that our numbers
are low. It is that **we have no number that a third party would accept.** Every coding
result is on a bank we mined ourselves, against a control the 2026 literature classifies as
near-random, at a retrieval endpoint nobody outside the program cares about. By the owner's
own standing rule — vendor self-run claims do not count — **our evidence is currently in the
same class as the vendor claims we discount.** The two head-to-heads against something we did
not build (MemoryCode, the Syndai docs gate) we **lost**.

## 3.4 The shortest credible path

| stage | what | cost | why this order |
|---|---|---|---|
| **1** | **Arm 0 + Arms 1–2: the controls.** Agentic-search and dense-RAG on Track R. | ~$15–45 | If we lose these, nothing downstream is worth buying. **Do not authorize $545 before knowing whether `grep` beats us.** |
| **2** | **Phase 3 reader QA** (already preregistered, `2026-08-01-phase3-coding-reader-qa.md`) | ~$142 ceiling | Converts retrieval margin into answer accuracy. Without it there is no endpoint anyone outside the program recognises. |
| **3** | **Arm 5: SWE-ContextBench `Related` n=376**, staged $0 → ~$40 → ~$504 | **~$545** | The claim. Second run of this instrument in existence; **nobody holds the first-mover slot.** |
| **4** | **Arm 4: agent-authored keys** | ~$3 | Cheap, best-powered lane, on the critical path. Run in parallel with 1–3. |
| **5** | Publish predictions + the ChainSWE arm | ~$300–500 | Two coding instruments beats one; ChainSWE's 48-point positional drop is the most dramatic public statement of our problem. |

**Total to a defensible public claim: ~$700–1,100, gated so that ~$45 decides whether the
remaining ~$650 is spent at all.**

**The claim to aim for, phrased so it cannot be inflated:**

> On SWE-ContextBench `Related` (n=376, census-complete, no instance selection), MemPhant
> memory moves Resolved from *X*% to *Y*% against the same scaffold and model, paired exact
> McNemar p=*p*, closing *Z*% of the gap to the published oracle-summary ceiling. Predictions
> and per-instance results are published.

That is falsifiable, third-party-runnable, and on a coding artifact. **"SOTA on LoCoMo" is
none of those things and should never be attempted.**

## 3.5 Is "SOTA on a public benchmark" even the right goal?

**Recommendation: no as the primary goal, yes as a gated secondary — and the two are the
same $545, so the choice is about framing, not budget.**

**The case against SOTA as primary.** The evidence is unusually one-sided. The best
deployed memory system on the only public coding memory benchmark buys **+4pp over doing
nothing**, and one named vendor buys **−2pp**. CL-Bench: **naive ICL beats every memory
system.** MemDelta: **agent self-memory 42% vs 47% for basic retrieval**, and **Mem0 matches
plain cloud RAG at 50× the cost.** SWE-Explore: **an agent with `grep` beats dense retrieval
7.6×.** ContextBench: *"sophisticated agent scaffolding does not necessarily improve context
retrieval performance"* — with **plain bash beating every embedding-based system in the
table.** Optimising to top a leaderboard in that field is optimising inside a regime where
the whole category is currently losing to the trivial baseline. And the coding-memory
leaderboard **does not exist**, so there is no ranking to top — only a paper table with n=99
and power 0.08.

**The case for measuring it anyway.** Three things only a public instrument gives us. (i) It
is the only evidence that escapes the self-run trap; our own rule says private banks do not
count, and every coding bank we own is private and self-mined. (ii) SWE-ContextBench supplies
a **measured oracle ceiling** (23.40% on `Related`), turning "are we good" into "what fraction
of the achievable gap do we close" — the only honest form of the question. (iii) It is the
**contamination and saturation guard**: the tranche-1 pathology (3 of 4 no-memory baselines
already resolving) is exactly the failure a public baseline catches and a private bank hides.

**The recommendation, concretely.**

1. **Primary goal: measurably better for Syndai's coding agent**, where "better" means
   beating the **agentic-search control** (Arm 1) — the actual production alternative — on
   answer accuracy at a stated token and latency cost. That is the number that decides whether
   MemPhant ships into Syndai, and no public leaderboard measures it.
2. **Gate the public claim on the private one.** If we cannot beat `grep` on our own corpus
   for ~$40, we will not beat a no-memory baseline on SWE-ContextBench for $545. **Arm 1
   before Arm 5, without exception.**
3. **Then buy the public claim, and phrase it as gap-closure, not as SOTA.** The first-mover
   slot on the only coding-memory instrument is genuinely open — no leaderboard, no vendor
   reproduction, nine citing papers with zero runs. That is worth $545 *once we have earned
   it internally*.
4. **Do not pursue LoCoMo, LongMemEval-V2, or any leaderboard-free chat memory benchmark.**
   LoCoMo is non-commercial and 6.4% wrong; LongMemEval-V2 is web-agent trajectories with a
   two-source numeric disagreement; neither contains coding work.
5. **Consider building the instrument.** Every source consulted says the same thing: **the
   coding-cross-session-memory benchmark slot is empty.** The sequential coding benchmarks
   (SWE-ContextBench, ChainSWE, SWE-Bench-CL, SWE-Milestone) give task *pairing* but **no
   "what should have been remembered" annotation**; the memory benchmarks that have such
   annotations (MemoryArena, MemoryAgentBench) contain **zero coding tasks**. Publishing a
   licence-clean coding-memory instrument — most plausibly SWE-chat's 205 multi-session repos
   plus ContextBench-style gold-context annotation — would be a more durable position than
   any score on someone else's table, and it is the one asset a competitor cannot self-run
   their way around. **Not proposed as work now.** It is the strategic option this research
   surfaced, and it should be a deliberate decision rather than a discovery.

---

## Appendix — what remains unverified

- **SWE-ContextBench licence:** our stricter reading (no LICENSE file anywhere;
  `RECORD_METADATA`, `verified: false`) stands over an independent sweep that graded the same
  card field *verified*. Not redistribution-clear.
- **ChainSWE, SWE-chat, Multi-SWE-bench_trajs, TerminalBench-trajectories licences:** HF
  `cardData` field only **[C]**, not a licence artifact. For third-party re-uploads this is a
  claim over data the uploader did not author. **Apply the memorycode standard — fetch a real
  LICENSE — before any of these carries a decision.**
- **TraceLab's CC-BY-4.0** is a paper claim; the licence artifact was not fetched.
- **`gemini-embedding-2` MTEB-Code: 84.0 (own abstract) vs 74.66 (secondary). Unresolved —
  do not quote either.**
- **CORE-Bench's rewrite deltas conflict internally** (prose says rewriting hurts; extracted
  table rows show it helping for CodeRankEmbed). Read Table 3 directly before relying on a
  signed delta. Its BM25 baseline — the number most comparable to our lexical channel — was
  not extractable.
- **`ettin-reranker-17m`'s INT8 2× speedup is inferred, not published**, and it is the
  load-bearing assumption in the only plausible sub-200 ms rerank path.
- **No index-size ratio for multi-vector retrieval on a code corpus is published anywhere.**
- **No first-party 2026 Cursor / Cognition / Anthropic / OpenAI engineering post on
  retrieving over session history could be fetched.** Circulating architecture claims are
  third-party blogs — plausible, UNVERIFIED, not citable.
- **MTEB's live Code leaderboard is JS-rendered and could not be read**; every code number
  here comes from model cards and papers. CoIR's leaderboard is live but ~a year stale.
  **CoREB is the one to track.**
- Aggregator sites (llm-stats, morphllm, codeant, atlan, particula, codesota) returned numbers
  and even model names that could not be confirmed on any primary page. **Treat as
  fabricated-by-aggregation.**
