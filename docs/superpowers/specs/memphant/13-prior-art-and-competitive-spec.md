# MemPhant - Prior Art and Competitive Spec

## 0. Read This Correctly

**Amended 2026-07-30 (owner decision).** The former blanket rule — "copy good
architecture patterns, not code" — is rescinded. It was stricter than
`26` §8, which already records per-project REUSE-OK / LANDMINE verdicts, and it
was costing us adoptable permissively licensed work for no legal reason.

The rule is now **license-governed, not category-governed**:

- **Code reuse is permitted from MIT / Apache-2.0 / BSD / PostgreSQL-licensed
  projects**, with attribution per the license, whether or not the project is a
  competitor. Record source, license, and the exact commit/revision at the point
  of reuse, and carry the required NOTICE.
- **Forbidden to vendor: AGPL, GPL, LGPL, SSPL, BUSL, Elastic License 2.0, and
  any non-commercial license (CC-BY-NC).** Architecture study is always allowed.
  Standing landmines are enumerated in `26` §8.
- **Competitor *products* remain excluded as runtime dependencies.** Using a
  competitor's Apache-2.0 source is reuse; depending on their hosted service is
  a dependency. The no-competitor-dependence position was always about the
  latter — MemPhant must stand up without anyone else's service running.
- **Model weights carry their own license, separate from the repository's.** A
  permissively licensed repo with non-commercial weights is a trap; check both
  and record both.
- Re-verify from the LICENSE file, never a README badge, and never a cached
  belief — projects relicense.

All competitor claims are still re-verified before public use.

## 1. Competitor Map

| Project | Strength | Gap MemPhant targets |
|---|---|---|
| Mem0 | DX, adapters, broad adoption. | Less rigorous raw-episode/provenance/poisoning posture. |
| Zep / Graphiti | Temporal graph memory. | **Requires a graph engine as a mandatory production dependency** (Neo4j 5.26 / FalkorDB / Neptune backends; no Postgres driver; verified). MemPhant serves the same bitemporal-graph use cases via relational edges in Postgres — no graph engine, no second datastore. Still a real, verifiable differentiator vs Graphiti; but the sharpest collision on the single-Postgres axis is now cognee 1.0 (Postgres-native backend option, §1.1), not Graphiti. |
| Letta | Agent runtime with stateful memory. | Full runtime; MemPhant should stay memory substrate. |
| Hindsight | Strong hybrid retrieval; LongMemEval-anchored posture with independent reproduction (BEAM de-emphasized). | **Postgres-backed too** (embedded pg0 default) — "we run on Postgres" is not a differentiator vs Hindsight. Differentiate on Apache-2.0 Rust core + tree-scoped poisoning defense + governed substrate. |
| Cognee | Memory/control-plane over graph/vector stores. | Broader control plane; MemPhant should be smaller and sharper. |
| AGENTS.db / gbrain | Local-first context stores. | Useful local patterns, but MemPhant targets production scoped memory. |

## 1.1 Repo/License Snapshot (GitHub-verified 2026-07-02)

Stars/license/activity GitHub-verified 2026-07-02 via the GitHub API; re-verify at ingestion (numbers move). Calibration update (2026-07-02): star counts in the agent-tooling adjacency have inflated to 100k–380k★ (§1.4) — "implausibly large stars" is no longer a fabrication signal; verify via the API, never vibes.

| Repo/project | Stars | License | Activity | Useful pattern | Avoid |
|---|---|---|---|---|---|
| `mem0ai/mem0` | ~59.9k | Apache-2.0 | daily | Python/TS SDKs from one schema, MCP server split, cookbooks; v3 multi-signal retrieval (semantic + BM25 + entity, fused) + retrieval-time temporal reasoning | v3 is single-pass **ADD-only** extraction (one LLM call, no UPDATE/DELETE — "memories accumulate; nothing is overwritten"); OSS **graph module removed** (graph gated to Pro/Enterprise; their own 2026-07-01 report concedes the removal is "a regression" for graph-traversal users); community fallout in issue #5352 (time-blindness, semantic conflicts, missing CRUD); self-reported LoCoMo 92.5 / LongMemEval 94.4 / BEAM 64.1 @1M, 48.6 @10M (vendor_reported, `12` §8); lossy extraction as ground truth; no bitemporal/provenance |
| `getzep/graphiti` (the engine) | ~28.3k | Apache-2.0 | **engine dev paused** (only CLA-bot commits since 2026-06-25; last release v0.29.2 2026-06-08) | bitemporal edge validity (`valid_at`/`invalid_at`), provenance-to-episode, MCP split; new `graphiti-core[falkordblite]` embedded option softens the second-datastore critique slightly (still a graph engine) | graph engine required (Neo4j 5.26 / FalkorDB / Neptune backends, **no Postgres driver**); Kuzu driver **deprecated** ("upstream no longer maintained") — the "optional graph DB" escape valve is closing |
| `getzep/zep` (examples/SaaS) | ~4.7k | Apache-2.0 (repo) | active | integration breadth | this repo is examples only; the **Zep product is closed-source SaaS** — compete against Graphiti (open engine), not "Zep" |
| `letta-ai/letta` (MemGPT) | ~23.6k | Apache-2.0 | main repo coasting (README-only commits; last release 0.16.8 2026-05-14); org energy moved to `letta-code` (~2.8k★, daily), `claude-subconscious` (~2.8k★, MIT — background memory consolidation for Claude Code), `agent-file` (.af format, ~1.2k★) | generated clients, API discipline, context-block separation; sleep-time compute shipped at v3 (`sleeptime_multi_agent_v3.py`) | full agent runtime; direction is now **learned memory managers** ("Memory Models: Towards Agents That Learn", 2026-06-25 — memory models trained with memory-native RL, memory that "transfers across model generations"). The field bifurcation: learned memory manager (weights) vs governed substrate (data) — MemPhant positions as the substrate a learned memory model reads/writes through, not its competitor |
| `vectorize-io/hindsight` | ~17.9k | MIT | hottest repo in set (~75 commits/wk) | channelized semantic/lexical/graph/temporal recall + fusion; now LongMemEval-anchored ("SOTA as of January 2026", independently reproduced by Virginia Tech Sanghani Center + The Washington Post; paper arXiv:2512.12818; BEAM de-emphasized) — a reproducibility bar to match; v0.8.2 (2026-06-12) shipped Memory Defense (45-pattern pre-storage scrubbing: keys/secrets/PII, redact-or-block), Reversible Memory Curation (edit/invalidate/revert with history), Observation Scopes = **trust-feature parity pressure**; coding-agent plugin blitz 06-25→07-01 (claude-code, cursor-cli, aider, openhands, continue, langgraph, devin-desktop) | **not a Rust core** (Python 12.8MB / TS 2.5MB / Rust 398KB — Rust is `hindsight-cli` only) and **Postgres-backed** (embedded pg0 default; external PG compose; Oracle AI DB enterprise option) — Postgres is not the differentiator vs Hindsight (§1); managed-cloud-first distribution; copying launch surface. MIT = no patent grant (mild) |
| `topoteretes/cognee` | ~26.5k (+4.5k in one week — fastest riser) | Apache-2.0 | daily; **full 1.0 rewrite** (v1.0.0 tag 2026-04-11; 2026-06-26 was the "Cognee 1.0" blog announcement, shipped as v1.2.2) | extras-based packaging, eval framework; 1.0 memory-native API (remember/recall/improve/forget), **single-Postgres backend option** (own Postgres graph backend + pgvector; recommended deploy default; vendor_reported ~10% faster than split graph+vector in their CI), Rust SDK (`topoteretes/cognee-rs`, thin client — core stays Python), TS SDK, COGX export format | the old "networkx default (in-memory)" critique is **stale** — local-dev defaults are now SQLite+LanceDB+Kuzu, Postgres the recommended deploy. **The sharpest collision** with MemPhant's "Postgres, no graph engine" differentiator; broad control plane; vendor_reported: ~6M memories/month across 100+ companies, BEAM 79% @100K / 67% @10M |
| `neuml/txtai` | ~12.7k | Apache-2.0 | active | packaging discipline | framework, not memory-native |
| `BAI-LAB/MemoryOS` | ~1.5k | Apache-2.0 | **dormant** (no commits since 2026-04-28) | OS-tiered hot/warm/cold retention (maps to our DSR tiers); EMNLP 2025 Oral | research prototype; OS metaphor ≠ proof |
| `kingjulio8238/Memary` | ~2.6k | MIT | **dead (last push 2024-10)** | conceptual framing only | historical reference, not a live competitor |

Re-verify license and repo contents before public comparison or code reuse.

## 1.2 2026 Newcomers (the spec must not miss)

| Repo/project | Stars | License | Why it matters |
|---|---|---|---|
| `MemPalace/mempalace` | ~56.9k | MIT | **Star leader** (created 2026-04-05; v3.5.0 2026-06-23; pushed daily). Local-first, verbatim storage, spatial wings→rooms→drawers hierarchy, ~170-token activation, backends ChromaDB(default)/SQLite/Qdrant/pgvector, 35 MCP tools, temporal entity graph in SQLite. Vendor claims (vendor_reported): LongMemEval 96.6% R@5 / 98.4% hybrid, LoCoMo 88.9% R@10. **Debunked twice**: arXiv:2604.21284 (performance "primarily from verbatim storage + ChromaDB default embedding"; hierarchy "operates as standard vector database metadata filtering"; the 4 real contributions: verbatim-first, ~170-token activation, deterministic offline, spatial metaphor) and its own issue #125 independent end-to-end eval (BEAM-100K **answer quality** 49.0% with raw ChromaDB k=10; **every proprietary mode below the raw baseline** at 26–28%; LoCoMo "100%" structurally guaranteed by top-k ≥ corpus size; weakest: event ordering 32%, summarization 35%, contradiction resolution 40%). THE retrieval-metric≠answer-quality case study (`12` §8). |
| `volcengine/OpenViking` | ~26.3k | **AGPL-3.0 LANDMINE** | ByteDance "self-evolving context database" unifying memory+RAG+skills under a filesystem paradigm (ls/cat navigation). Python. Cannot copy code (§1.3). |
| `supermemoryai/supermemory` | ~28.1k | MIT | Memory/context engine; `npx supermemory local` runs an embedded graph engine + local embeddings with the same API as hosted. TS, cloud-first. |
| `rohitg00/agentmemory` | 24,468 (created 2026-02-25 — ~24k★ in ~4 months, fast riser) | Apache-2.0 | "#1 Persistent memory for AI coding agents" — aimed squarely at the coding-agent use case (our Syndai lane); hooks/MCP/REST/Docker surfaces, iii engine; v0.9.27. |
| `MemoriLabs/Memori` | ~15.5k | Apache-2.0 (LICENSE verified; GitHub NOASSERTION = detector failure) | Push cadence cooling. |
| `memvid/memvid` | ~15.7k | Apache-2.0, **Rust** | **STALLED — zero commits since 2026-05-27** (demoted from "the most direct collision"). Rust + Apache-2.0 + "no database" single-file portable memory (immutable "Smart Frames", sub-ms P50); if it revives, differentiate on multi-tenant isolation + poisoning defense + Postgres-backed provenance, which a single file cannot provide. |
| `NevaMind-AI/memU` | ~14.0k | Apache-2.0 (verified) | Active commits, stale releases. |
| `EverMind-AI/EverOS` | ~10.0k | Apache-2.0 | Local-first: Markdown-as-source-of-truth + SQLite + LanceDB. |
| `TencentCloud/TencentDB-Agent-Memory` | ~6.4k | MIT (LICENSE verified; GitHub NOASSERTION = detector failure) | 4-tier progressive memory; symbolic compression of tool logs (maps to our belief tier); GA v1.0.0 2026-06-11, low velocity; benchmarks self-reported. TS-only, no Rust core. |
| `campfirein/byterover-cli` (ex-Cipher) | ~4.9k | **Elastic License 2.0** (verified verbatim) | Coding-agent memory (our Syndai use case); velocity now stale (~1 commit/wk, no release in 5 wks) — **LICENSE LANDMINE: cannot copy code and cannot offer as a hosted service.** Study architecture only. |
| `memodb-io/Acontext` | ~3.6k | Apache-2.0 | "Agent Skills as a Memory Layer" — skills-as-memory convergence signal. |
| `MemMachine/MemMachine` | ~3.2k | Apache-2.0 | Now a **real repo** (promoted from the §3.1 paper signal): episodic memory in a Neo4j graph + profile memory in SQL, Python. |
| `zilliztech/memsearch` | ~2.2k | MIT | Zilliz/Milvus-backed memory for coding agents (Claude Code, Codex) — our Syndai lane. |

## 1.3 License Landmines (cannot copy code from)

- **`campfirein/byterover-cli` — Elastic License 2.0**: prohibits providing the software as a hosted/managed service. Architecture study only.
- **`volcengine/OpenViking` — AGPL-3.0**: cannot vendor or copy code. Architecture study only.
- **`plastic-labs/honcho` — AGPL-3.0**: cannot copy code (tracked in §3.1 as the actual BEAM-10M #2).
- **Smithery CLI — AGPL-3.0**: cannot vendor.
- **`getzep/zep` product — closed-source SaaS**: only Graphiti (Apache-2.0) is reusable.
- **pgvector — PostgreSQL License (permissive, OSI-approved, *more* permissive than Apache-2.0)**: GitHub's `NOASSERTION` label is a detector failure, not a licensing problem. Safe to build on (infra dependency, `26`).

## 1.4 Harness Layer (the distribution shelf)

The memory battle moved **inside the mega-harnesses** (GitHub-verified 2026-07-02/03): memory is now built-in and on-by-default at the harness layer. That reframes distribution — a substrate wins by being the **governed backend the harnesses plug into**, where a plug point exists.

| Harness | Stars / license / version | Built-in memory | Plug point |
|---|---|---|---|
| `openclaw/openclaw` | 381,443★ / MIT (LICENSE © OpenClaw Foundation; GitHub tags NOASSERTION) / v2026.6.11 | per-agent SQLite + FTS5/BM25 + hybrid vector; QMD local-first search sidecar (BM25+vector+rerank); blocking "active memory" sub-agent (proactive recall before reply); opt-in "dreaming" background consolidation (light→REM→deep; only deep writes MEMORY.md); `memory-wiki` compiler emitting structured claims frontmatter + freshness + a contradictions dashboard — a claims-ledger sibling, but a compiled read-back artifact is a drifting second source of truth if agents consume it: the anti-pattern MemPhant's compiled export avoids by being read-only + staleness-verified | **NONE — recorded NON-TARGET** (builtin, free, default; those users are unreachable and were never the market) |
| `NousResearch/hermes-agent` | 207,822★ / MIT / v2026.7.1 (2026-07-01) | autonomous agent harness; MEMORY.md/USER.md curated memory; skills-as-procedural-memory | **pluggable memory-provider SPI, one active provider at a time; 8 providers shipped** (honcho, mem0, byterover, hindsight, holographic, openviking, retaindb, supermemory) — six are competitors mapped in §1.1/§1.2, all sitting on a shelf MemPhant must be on. The specced MemPhant Hermes provider adapter (`08` §5.1b, activation-gated) is the second platform-convention adapter under the R79 rule (the first: the `memory_20250818` file adapter, `08` §5.1a) |
| Claude Code auto-memory | GA, **on by default** (v2.1.59+) | Claude writes MEMORY.md per project (build commands, debugging insights, architecture notes, style prefs; first 200 lines/25KB loaded per session); `/memory` command. Distinct from user-written CLAUDE.md and from the API `memory_20250818` tool (which MemPhant's file adapter already targets) | **the file-tool handler — shipped as R79** (`08` §5.1a) |
| `gastownhall/beads` | 25,035★ (transferred from steveyegge/beads) / MIT | Dolt-backed issue/task DB; `bd prime` injects ~1-2k tokens via Claude Code hooks | signal: coding agents want memory tied to **work objects** (validates scope `external_ref` as the work-object anchor); **recorded exclusion**: work-object tracking is a workflow-engine non-goal (`02` §8) — study the pattern, never absorb the tracker |
| `garrytan/gbrain` | 24,817★ / MIT | per-repo trust modes (read-write/read-only/deny) + cross-machine sync + secret scanning | a real precedent for per-scope trust-policy UX (distinct repo from `garrytan/gstack`, 118,887★ — an earlier report conflated them) |
| `obra/superpowers` | 244,133★ / MIT | skills library; artifact-as-memory pattern | demand-signal row: its issue tracker documents the unmet needs (project memory, cross-session/subagent continuity, token bloat) |
| `bmad-code-org/BMAD-METHOD` | 49,999★ / MIT | agentic planning method; artifact-as-memory pattern | demand-signal row: same unmet-need class documented in its issue tracker |

**Completeness rule (deterministic):** any project ≥50k★ verified during a review pass either appears in this doc or gets a one-line recorded exclusion reason. (Added after Pass 16 found the two most-starred adjacent projects absent from a doc stamped "GitHub-verified".)

### 1.4a `marcusquinn/aidevops` — append-only memory with outcome-gated promotion (studied 2026-08-09; MIT + attribution-on-patterns `ATTRIBUTION.md` → study/reimplement only, star count un-verified this pass)

A live open-source agent-devops harness whose memory subsystem is the closest external analogue to MemPhant's governed-substrate thesis on a *small* substrate: **SQLite FTS5** (BM25, opt-in local/OpenAI embeddings + RRF hybrid) fronting a canonical `observations` envelope with **append-only truth maintenance** — `updates`/`extends`/`derives`/`debunks` relations recorded as `learning_truth_events` linked to evidence memories (never in-place edits), one-source-per-evidence dedup (replay cannot manufacture independent evidence), and default recall that hides debunked/retracted/superseded rows **before** access tracking. It layers three mechanisms MemPhant lacks a shipped analogue of: a retrieval-outcome "Q-value" (`bm25 − usefulness*0.3`, Ori-Mnemos-derived); and **outcome-gated, reversibly-promoted** always-loaded guidance — a memory graduates only on an independently verified `test_passed|pr_merged|operational_verified|verified_reuse` outcome carrying a non-self verifier identity, wrapped in per-entry `begin/end` markers with a destination-keyed promotions record so revocation is a surgical block delete. It is a genuine peer on *append-only + truth-maintenance + outcome-gated promotion*, and confirmation that these patterns are viable in production; it is **not** a substrate collision — SQLite FTS5 is single-bank per runner (namespaces aside), no bitemporal transaction/valid-time split, no partitioned per-tenant HNSW, no trust-capped high-risk suppression. The three mechanisms were evaluated for adoption in `26` §9 (adopt the filter-before-track invariant; dormant-gate the Q-value; Phase-A-condition the outcome-gated injection block); this row is the competitive/prior-art record independent of those decisions.

## 2. What To Copy

- Hybrid retrieval: lexical + semantic + temporal/graph + rerank.
- Ground-truth episode retention.
- Bi-temporal facts.
- Provenance to source episodes.
- MCP distribution.
- SDK-first developer experience.
- Eval scorecards with speed and cost.
- Generated SDKs from a canonical API schema.
- MCP as a separately testable server.
- Cookbook examples that prove practical adoption.
- Filesystem/git-backed memory ideas for coding-agent procedural resources, if traces justify them.

## 3. What Not To Copy

- Single-bank memory for every actor.
- Lossy extraction as ground truth.
- Hidden benchmark configs.
- Memory writes with no source trust.
- Full agent runtime creep.
- Huge adapter matrix before core quality.
- Vendor-reported benchmark claims as marketing proof.
- Biology language as proof of quality.
- Full control plane or agent runtime scope creep.

## 3.1 Research Signals To Track

| Work | Signal | Spec response |
|---|---|---|
| MemMachine | store whole episodes and contextualize retrieval (now a real repo — see §1.2) | raw episodes are ground truth |
| Hindsight | parallel semantic/lexical/graph/temporal recall with fusion | channelized traces and RRF |
| Graphiti/Zep | temporal fact validity and provenance | bitemporal semantic memory |
| Letta Context Repositories/MemFS | git/file-backed memory for coding agents | resource/procedural memory seams |
| ReasoningBank | strategy distillation from successes/failures | procedural candidate/promotion path |
| Letta sleep-time compute | named, shipped background-consolidation pattern (idle-time agent) | grounds the §9 `04` consolidation cycle in 2026 production practice (de-risks "biology as proof") |
| Hendrickson: "agent memory breaks at 500K, not 10M" | the field over-indexes on 10M-token *retrieval*; correctness-after-*update* (entity resolution, silent overwrites, lost corrections) breaks far earlier | strongest external validation of our correction/bitemporal/contradiction thesis (`04` §3.1) — cite it |
| HippoRAG2 / Honcho | neuro-inspired PageRank retrieval (HippoRAG2); Honcho is the actual #2 on BEAM-10M | track as retrieval comparability + an honest competitor on the leaderboard we decline to anchor to |
| MemCog | proactive navigable memory may improve hard tasks | L4/deliberate recall feature flag |
| OWASP Agent Memory Guard | memory contracts and poisoning controls | trust/security gates and evals |
| GEM / MemState | memory correctness as state trajectory with ingestion, revision, forgetting, retrieval operators | first-class `retain`, `correct`, `forget`, and `recall` public operations |
| FSRS/open-spaced-repetition | DSR-style retention modeling evolves | FSRS-inspired, not fixed-version |
| Eywa (arXiv:2605.30771, **single-author**) | "evidence before belief": immutable source evidence, typed-signal validation, deterministic zero-LLM multi-route read path; publishes full per-question artifacts | near-identical architecture bets — cite as convergent prior art; differentiate on Rust/Postgres/multi-tenant/governance; its artifact publication is a reproducibility bar worth matching |
| MemIR (arXiv:2605.25869) | names "provenance-role collapse" (source-monitoring errors from unstructured history); typed Memory Intermediate Representation; "factual authorization" restricted to supported claims | external validation of the typed-provenance design |
| MemForest (arXiv:2605.23986) | write-path construction throughput as a headline metric (79.8% pass@1 LongMemEval-S; ~6x construction throughput vs EverMemOS) | a metric a Rust/COPY-friendly system should win and report |
| AutoMEM (arXiv:2606.04315) | cross-scenario diagnostic: an agentic harness managing **flat text files** through tool calls outranked eight memory systems; rankings flip across scenarios | reinforces the filesystem control baseline; report cross-scenario variance (`12` §2.0) |
| FSFM (arXiv:2604.20300) | biologically-inspired selective-forgetting taxonomy (passive decay / active deletion / safety-triggered / adaptive RL) | taxonomy worth tracking; treat its "100% elimination of security risks" claim with suspicion |

## 4. Competitive Claim Rules

Allowed:

- "Apache-2.0 Rust-first memory substrate."
- "Poisoning defense and traceable evals are core design pillars."
- "Syndai is the first dogfood customer."

Not allowed without measured proof:

- "best memory system"
- "SOTA"
- "beats Hindsight/Mem0/Zep"
- "poisoning-proof"

## 5. Moat Hypothesis

The moat is not one algorithm. It is the combination of:

```text
developer trust from Apache-2.0
+ operational trust from Rust/Postgres
+ safety trust from poisoning controls
+ evidence trust from traces/evals
+ product trust from Syndai dogfood
```

If one of those is weak, narrow the claim.

Two GitHub-verified facts (2026-07-02) sharpen the moat. First, the **Apache-2.0 Rust-substrate lane is open**: no Rust memory competitor above 250★ exists (mempal 223★, mentedb 102★) and memvid is stalled (§1.2). Second, the field is **bifurcating** into learned memory managers (Letta's memory-models direction — the manager lives in weights) versus governed substrates (the memory lives in governed data). MemPhant claims the substrate side: the governed store a learned memory manager reads and writes through, not its competitor (§1.1).
