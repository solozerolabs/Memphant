# MemPhant - Final Decision Register

## 0. Purpose

This file records final launch-critical decisions. If another doc conflicts with this register, this register wins and the other doc must be patched.

## 1. Core Architecture Decisions

| Decision | Final choice | Reason |
|---|---|---|
| Public repo | Apache-2.0 open core from day one | adoption and enterprise trust |
| Primary language | Rust core/server/CLI/MCP/eval runner | deterministic hot paths, deployment, safety; WS-0 R83 spike measured warm no-recompile Rust policy-change iteration at 0.073× Python, below the <1.5× proceed threshold |
| Primary production integration | HTTP API plus generated SDKs | stable public contract |
| Python integration | Pure HTTP SDK now; PyO3/maturin native wheel deferred | Python adoption without placeholder native packaging; native waits for a real embedded/local API |
| TypeScript integration | generated HTTP SDK | web/Node agent adoption |
| MCP | stdio and Streamable HTTP | local and hosted agent integration |
| Store | Postgres 17/18-compatible plus pgvector ≥ 0.8.4 | production-grade and portable (R74) |
| Local dev store | Docker/plain Postgres | avoids second store semantics |
| External graph DB | rejected for first public architecture | relational edges cover v1 without another dependency |
| Object store | required for raw episodes/resources over inline size limit | raw capture without bloating Postgres |
| ID type | UUIDv7 for public IDs and primary keys | sortable, globally unique, Postgres-friendly |
| Time type | timezone-aware UTC timestamps | reproducibility and bitemporal facts |
| Physical partitioning | `PARTITION BY HASH(tenant_id)`, modulus set-once-immutable (64 hosted / 4–8 BYOC / **1 = plain unpartitioned table, no `PARTITION BY`**); partitioning is opt-in (>1 only); no pg_partman, no Citus | per-partition local HNSW fixes small-tenant filtered recall; `tenant_id` isolation key stays in every deployment; single-tenant self-host pays zero partitioning overhead; retrofit = full table rewrite (`04` §7.0) |
| Vector index dim cap | `halfvec` HNSW ≤ 4,000 (NOT 2,000); per-profile partial indexes; dimensionless `vec` column | corrects the `vector`-vs-`halfvec` confusion; mixed-profile coexistence (`02` §2.1a) |
| Schema evolution | `schema_compat_revision` boot-floor (Synapse pattern) + additive-vs-breaking taxonomy + forward-compat read contract | no-CLA forks need an in-data contract, not a central coordinator (`25` §11b/§11c) |
| Two-store durability | content-addressed blobs; GC marks from Postgres reference set + `blob_ledger`, never `object_store.list()`; `MIN_AGE` grace closes the write-commit race | one inequality (`max_txn ≪ MIN_AGE`) is the whole proof; `MemoryStore` gains a txn seam (`02` §2.3, `03` §4) |
| Scope tree + inheritance | adjacency (`parent_scope_id`) + cached `materialized_path` `ltree` (GiST `@>` walk, no hot-path recursion), depth ≤ 32; inheritance-policy = typed `scope_policy` table, deny-by-default; grant = explicit row, NEVER a `memory_edge`; `scope`/`scope_policy`/`agent_node` unpartitioned (tree, not memory — §7.0 carve-out) | read-heavy recall wants indexed ancestor resolution; makes "no implicit sibling access" falsifiable (`04` §11.0) |
| Resource chunk identity | a chunk IS a `kind='resource'` `memory_unit`; `embedding` keys on `memory_unit_id`, no chunk table/grain; `resource.acl` is an in-stage narrowing gate, not a parallel engine | avoids a frozen-PK rewrite + split embedding grain at 10M scale; closes the chunk-recall ACL leak (`04` §6.1, `03` §5.2) |
| Bitemporal write + recall discipline | transaction-time **append-only**: close-generation + INSERT for `correct`/supersede/invalidate; never in-place `valid_*` mutate; current-generation partial index (`transaction_to IS NULL`); recall resolves independent `transaction_as_of` + `valid_at` half-open axes before top-N (transaction-time gates every kind; valid-time gates only the bitemporal `semantic`/`belief` kinds, never the null-`valid_*` episodic/procedural/resource units) | makes audit replay unambiguous and preserves non-overlapping represented-world history (Fowler/SQL:2011/XTDB); fixes the §3.4 in-place bug (`04` §7.3a) |
| Cross-store restore/PITR | Postgres PITR authoritative; bucket reconciled by presence, never rolled back; object-store retention ≥ PITR window; GC suspended until post-restore sweep validates the reference set; quiesce writes until the integrity gate passes | content-addressing makes restore a presence problem, not a version-merge; crypto-shred correct across all restore points (`02` §2.3, `14` §4.2, `25` §7a) |
| Encryption & crypto-shred | 3-tier envelope: per-user DEK ← per-tenant KEK ← KMS/TEE root KEK; encrypt `body`/blobs only (vectors plaintext; `exact`-profile opt-in to encrypt); plaintext keys never in Postgres (wrapped DEKs + KMS refs); BYOC customer holds own KEK | per-user DEK ⇒ "forget user X" by key destruction; HNSW needs plaintext vectors (arXiv:2508.10373); crypto-shred complements tombstone+compaction, order = DEK→saga (`06` §6.1.1/§6.2) |
| Deployment posture | OSS Apache-2.0 library/core + a **closed managed hosted service** (Syndai = first dogfood tenant; external Pro/Team tenants); open core sufficient to self-host without Syndai | library is the product, hosted is the closed revenue layer (`09` §0.1/§9.1) |
| Multi-region residency | **cell-per-region**: open core stays single-region (immutable `tenant.region`, cross-region refused); hosted multi-region = N single-region cells + a no-PII tenant→region directory + an edge router (Fly `fly-replay`); migration = export→import, never live copy | KISS — no multi-region schema/replication/distributed-txn; residency is a closed-layer composition of single-region cells; library carries zero multi-region machinery (`25` §7b) |
| Hosted runtime | **full backend on Fly Machines** — the same single static binary self-hosters run (`memphant-server` + `memphant-worker` process groups, bluegreen, doppler-run boot); Supabase = Postgres + Storage only, never compute; **Supabase Edge Functions REJECTED for core** (no Deno layer anywhere; the only edge component is the thin `fly-replay` router) | the Rust core, advisory-lock reflect leases, pgmq consumers, Temporal workers, `spawn_blocking` pools, and stateful MCP sessions are structurally impossible on per-request isolates; an edge layer would fork invariant #11's one-binary hosted=self-host contract (R93, `25` §7b) |
| Tenancy primitive | keep `tenant_id` isolation in the open core (2026-standard — every vector DB + Letta/Cognee bake one in); partitioning is **opt-in** (modulus 1 = plain table) so single-tenant self-host pays ~zero; `actor` (provenance) and `agent_node` (access-tree) stay distinct (orthogonal, not redundant) | isolation primitive is mandatory + irreversible; partition machinery is opt-in cost; tenant=isolation (core) vs account/billing (hosted) à la Temporal (`04` §7.0, `00` §2) |
| Hosted billing model | metered units (`recall_unit`/`storage_gb_month`-per-tier/`retain`/`reflect`+passthrough); quota→overage/degrade/cap + `billing_status` (suspend≠delete, export always free); **BYOC = flat control-plane fee, hosted = usage-metered** (opposite COGS); per-cell/per-tier COGS + gross-margin-per-tenant; Syndai = paying customer #1 | bill the dimensions now, defer prices until measured COGS; residency/erasure-SLA/DPA = Enterprise-billable, export stays free (`21` §1a/§2a/§3a/§3b/§7) |
| Escape-hatch principle | every frozen public contract has an internal **promotion-to-a-more-specialized-lane**: pgvector profile→dedicated vector engine; whale tenant→dedicated cell; hot subject→hot-current/audit split; kind enum→additive new kind; region→cell | the adversarial-review meta-lesson — the danger is the missing escape hatch, not the primitive (`02` §2.1b/§6.2, `04` §7.0/§7, `25` §7b) |
| Binary-quant dim floor | `hnsw_binary` forbidden below ~1024-d (raw bit recall collapses: 960-d=0%, 128-d~2.5%); always rerank; pays only ≥1536-d; `iterative_scan=relaxed_order` default on filtered recall | corrects "binary is a blanket scale lever" — it isn't below the floor (Katz/Qdrant/arXiv:2603.23710; `02` §2.1a) |
| Poisoning: provenance + anomaly | provenance is necessary-NOT-sufficient (query-only self-generated MINJA + Sybil have clean provenance) → add a MemAudit-style causal+structural anomaly layer (post-hoc) + Sybil-resistant `actor_id` + dual-guard + high-risk quorum | defense is layered, not provenance-only (`06` §3.2/§4.3) |
| Bitemporal tiebreak | authoritative ordering = DB-assigned `transaction_from` (DB clock/HLC), NEVER writer wall-clock; contradiction resolution = write-time typed contract with keyed audit of the LLM judge (TOKI) | wall-clock tiebreak is non-deterministic under skew; an LLM judge on the write path is replay-inconsistent without keyed logging (`04` §3.1/§3.4) |
| Crypto-shred completeness | erasure incomplete until vectors are **physically compacted out of the index** (plaintext embeddings invertible to PII, cross-model/training-free); GDPR = pseudonymisation hedge, "reduces recoverability" not "provably erased" | the index is the deletion boundary; key-shredding the body alone is a bypass (`06` §6.2) |
| **V1 build scope** | freeze EVERY interface (schema, flags, trace fields, verbs); **build = rungs 0–3 spine + citations + `correct`/`forget` + REST/MCP/Python SDK**; rung-4+ *behavior* (edge expansion, rerank, decomposition, DSR fold, procedural replay harness, L4) built only at its `27` rung activation | resolves the suite's one internal contradiction ("ship the methods from the first build" vs "activate behind gates"), found independently by two Round-9 reviewers; cut line + calendar envelope owned by `29` §2a (R73) |
| Rust-first preconditions | Rust core RETAINED after the WS-0 R83 two-language spike, with the iteration-loop rule intact: no accuracy-critical iteration may require a Rust recompile; prompts/weights/thresholds are versioned data. | measured artifact `docs/build-log/artifacts/ws0-two-language-spike.json`: Python policy-change median 0.034191s, Rust policy-change median 0.002485s, Rust/Python ratio 0.073× |
| Outcome feedback verb (`mark`) | new public verb `mark {trace_id, used_ids[], outcome}` — the producer of the `outcome_label` trace field; `review_event` rows captured from day one (labels cannot be backfilled), fold/decay engine at rung 11 | freezing a trace field with no producer is a socket with no plug; every unlabeled dogfood day destroys the rung-13/FSRS training data (R77) |
| File-memory compatibility adapter | ship a `memory_20250818`-compatible virtual-filesystem handler (Anthropic's six file commands, GA; OpenAI converged on the same file metaphor) projecting the typed store as `/memories` | ONE adapter to a platform convention is not the rejected *wide framework matrix*; it makes MemPhant a drop-in durable backend for file-memory agents and answers the local-first wedge without a second store (R79) |
| Consolidation events | event taxonomy reserved-with-shape (`memory.promoted/superseded/contradiction_detected/quarantined`, `reflect.completed`) + transactional outbox; **poll-cursor delivery first, webhooks later, build post-v1** | integrators need push-shape typing before SDKs calcify; full webhook delivery semantics stays deferred (R78) |
| pgvector version floor | **pin ≥ 0.8.4** (0.8.3/0.8.4 fixed HNSW vacuum corruption + maintenance errors) | `forget` is delete-heavy and every partition carries a local HNSW index — the vacuum-corruption class is directly load-bearing (R74) |
| Pinned scope block | **ONE content-editable pinned block per scope** (`04` §12): hard Stage-7 token sub-budget, never silently dropped (explicit labeled truncation), trust-capped (data only; never `high_risk_arg`-eligible; never corroboration), append-only versioned + audited, cleared by scope-`forget`, OP-Bench-gated | the Letta-block job is *guaranteed presence of editable content*; order-only pins break that promise and N guaranteed refs recreate the over-personalization harm; Syndai's production persona block is the proven shape (R88) |
| Executable memory (rule store / auto-trigger / `rules/evaluate` verb) | **REJECTED** — the need is served by the named safe subset: procedures-with-preconditions (recall-matched, replay-validated, injected as *recommendations*), `trusted_user` preference facts the runtime applies, and outbox events the runtime chooses to act on (`04` §4) | auto-execution converts memory poisoning into persistent code execution (MemoryGraft passes naive replay; MINJA has clean provenance); `rules/evaluate` ≡ `recall(kinds:[procedural])` — a synonym verb (R-answers, Round 10) |
| `materialize` verb / working-memory kind / server-side memory views | **REJECTED** — packs are recomputed through Stage-0 gates every read; `delta_since` + `breadth` + the pinned block cover the working-set need | a materialized view is memory copied at write: a later `forget`/policy change is not reflected in the copy — a deletion-completeness hole by construction (`04` §11.1) |
| Stored composite importance score | **REJECTED** — importance stays decomposed (trust ⊥ confidence ⊥ DSR ⊥ `mark` utility); consequence = protected categories + `arg_risk` + `desired_retention` priors (`04` §8.1) | a single scalar is unauditable and farmable — a repetition term structurally rewards an attacker for repeating themselves; trust-as-hard-ceiling resists exactly that (Round 10) |
| Memory-provider adapters (above-MemPhant) | Hermes memory-provider adapter **specced at an activation gate** (`08` §5.1b; first design partner / launch window), after the R79 file-tool adapter — one thin adapter per *platform convention*, each mapping onto the seven verbs with source-trust caps + Stage-0 gates intact. NOT frozen (zero retrofit cost over an SPI the harness owns). **Direction distinction:** storage SPIs *below* MemPhant remain rejected; provider adapters *above* (MemPhant-as-provider) are this lane | auto-capture belongs below the tool layer (the `08` §4.2 determinism principle applied to capture); six mapped competitors already sit on the Hermes shelf; a 2–3 adapter set is not the rejected "wide framework matrix" (R87) |

## 2. SOTA-Critical Retrieval Decisions

| Lever | Final choice |
|---|---|
| Lexical retrieval | Postgres FTS in v1 |
| Dense retrieval | pgvector dense embeddings in v1 |
| Fusion | deterministic RRF in v1 |
| Rerank | bounded rerank in v1; learned/cross-encoder rerank may be provider-pluggable |
| Temporal recall | validity/recency windows in v1 |
| Edge expansion | relational 1-hop expansion in v1 |
| Query decomposition | enabled in benchmark/Deep mode in v1 |
| Contextual chunks | generated during extraction in v1 |
| HyDE | rejected for v1 because hallucinated pseudo-docs blur evidence provenance |
| L4 deliberate recall | shipped as explicit Deep/benchmark mode, never default hot path |
| Procedure recall | shipped with validation status; no skill compiler required |
| Decay | DSR fields and fixed-prior update rule in v1; learned fitter data-gated |

## 3. Benchmark Decisions

| Decision | Final choice |
|---|---|
| Primary production-improvement target | STATE-Bench (neutral, memory-agnostic, no published memory-system SOTA yet — the best defensible *first* SOTA claim) |
| Primary public accuracy benchmark | LongMemEval-V2 (arxiv 2605.12493) |
| Scale benchmark | BEAM at 100K/1M/10M tiers — cite the primary paper **arxiv 2510.27246**; the `agentmemorybenchmark.ai` board is a vendor leaderboard (`vendor_reported`) |
| Security benchmark | the custom corroboration-farming / persistent-memory-poisoning suite is primary; AgentDojo is supplementary (it tests tool-call injection, not persistent memory poisoning, and is near-saturated) |
| Compatibility baselines | LoCoMo, LongMemEval-S, PersonaMem, LifeBench |
| Public claim bar | accuracy + CI + latency + token/cost + config + archived traces |
| Competitor evidence | independent reproduction preferred; vendor-reported numbers labeled |
| Golden tests | executable fixtures with expected IDs, citations, forbidden leaks, trace assertions |
| SOTA policy | no SOTA claim without paired ablations and security evals |
| SOTA ladder | `27-sota-ladder-and-validation.md` is the activation and proof contract |
| Restraint launch-gate instrument | **MemSyco-Bench** (MIT, pinned `XMUDeepLIT/MemSyco-Bench@c31e2c85`), substituted for OP-Bench (no runnable release) and PS-Bench (no license); thresholds unchanged — D-2026-07-30c below |

### D-2026-07-30c — Restraint launch gate: MemSyco-Bench is the instrument

**Decision.** The `27` §1 restraint launch gate is measured with
**MemSyco-Bench**. The canonical scorecard identity is
`"benchmark": "memsyco-bench"`. OP-Bench and PS-Bench remain *admissible*
instrument names for the same gate but are not runnable today.

**Why this was forced.** The gate contradicted itself. `27` §1 and
`docs/launch/restraint-launch-scorecard.json` named OP-Bench/PS-Bench, and
`tests/test_restraint_launch_gate.py` asserted
`scorecard["benchmark"] in {"op-bench","ps-bench"}` whenever status is `pass` —
so **a passing MemSyco run would have failed the gate's own contract test**,
while a passing run of either named instrument was impossible to produce.

**Why MemSyco and not a local approximation.** `26` §3 already forbids replacing
an unavailable public benchmark with a local approximation, and that decision
stands unamended: this is **not** a local approximation. MemSyco-Bench is a
complete, MIT-licensed, externally published official release (arXiv 2607.01071),
pinned by revision and per-file sha256 in `benchmarks/manifests/memsyco.lock.json`
with its **native** scorer, run through `scripts/run_restraint_bench.py`. It is a
substitution of one legitimate public instrument for two unavailable ones, which
is exactly the move `26` §3 permits and the reconstructed-scorer move it forbids.

**Why it measures the same construct.** MemSyco's five tasks are
objective-fact judgment (memory must be ignored as evidence), contextual scope
control, memory-vs-evidence conflict, valid-memory selection, and personalized
memory use. That is over-retrieval harm and memory over-trust — the axis the
gate exists to bound. It is not a perfect superset: OP-Bench's explicit
irrelevance/sycophancy/repetition taxonomy and PS-Bench's intent-legitimation
attack surface are **not** separately scored by MemSyco. Intent-legitimation
therefore stays tracked where it already lives, as a threat row in `06` §9 with
PS-Bench cited as published prior art, not as a gate we run.

**What did NOT change.** Every substantive threshold: relative drop vs
memory-free baseline ≤ 0.15; sample ≥ 50; paired-delta CI upper bound ≤ 0.15;
`05` §1.5 relevance gate mandatory on breach; pinned-block content (`04` §12)
in-scope; the promotion-provenance rule (packaged Postgres runtime, pinned real
corpora, executed scorer).

**Evidence status.** A five-task MemSyco smoke passed 5/5 at $0.23557035
(`docs/build-log/2026-07-15-memsyco-smoke.md`). That is a smoke, explicitly
promotion-ineligible. **The restraint checkbox stays unchecked**; this decision
makes a passing run *expressible*, it does not assert one.

**Reopen test.** A complete, legally usable official OP-Bench or PS-Bench
release appears — then it is added as a run instrument alongside MemSyco, not as
a replacement, and the two are reported separately (never pooled).


## 4. Security and Data Decisions

| Decision | Final choice |
|---|---|
| Memory as control flow | rejected; memory is evidence only |
| Tenant isolation | mandatory on every recall/write path |
| Browser/mobile DB access | rejected |
| Supabase BYOC | supported only through explicit schema/RLS/grant posture |
| Direct PostgREST memory table access | off by default; allowed only with tested RLS |
| Service/admin keys in SDK/MCP | forbidden |
| Correction | first-class `correct` operation; selector-based, auditable, no silent overwrite |
| Deletion | immediate recall hide plus deletion generation and completeness audit |
| Poisoning defense | write-time classification, quarantine, read-time labels, high-risk suppression |
| Telemetry | IDs/counts/timings by default; raw memory only in tenant-governed traces |

## 5. Open Source and Governance Decisions

| Decision | Final choice |
|---|---|
| Contribution attestation | DCO with inbound=outbound Apache-2.0 |
| CLA | rejected at launch |
| Code of conduct | Contributor Covenant |
| Security policy | required before public repo |
| Public/private split | core/server/MCP/SDK/evals public; billing/control plane/private corpora may be closed |
| Syndai advantage | prohibited; Syndai uses public contracts |
| Benchmark disputes | public changelog and score deltas; no silent edits |

## 6. Syndai Integration Decisions

| Decision | Final choice |
|---|---|
| Syndai integration path | backend -> MemPhant SDK/API -> MemPhant service |
| Mobile/web path | mobile/web -> Syndai backend only |
| Direct DB coupling | rejected |
| Cutover | export, trace compare, first surface, full cutover, delete replaced paths |
| L0/L1+ policy | preserve Syndai L1+ block contract through neutral `agent_node` policy |
| Syndai source contract | `28-syndai-code-contract.md` owns checked backend invariants and required fixture families |
| Failure handling | keep raw episode export and golden cases; fix MemPhant before switching more surfaces |

## 7. Explicit Non-Goals

These are explicit non-goals:

- external graph DB as a default dependency
- SQLite/PGLite adapter
- large framework adapter matrix
- agent runtime
- workflow engine
- governed-action executor
- vendor leaderboard business
- CRDT/Yjs skill editor
- agent-native billing

They can only be reopened by (a) benchmark traces showing the current architecture cannot achieve the target, **or (b) distribution evidence that the adoption target is unreachable through the specced channels** (R86 — a distribution gap can never produce a benchmark trace; the register must not be category-blind on adoption). Prose reports satisfy neither test.

## 8. OSS Dependency & Prior-Art License Register

MemPhant is Apache-2.0, so every reused component and studied competitor is license-checked (GitHub-verified 2026-06-25, `13` §1.3). Clean-room verdict = whether MemPhant may copy code (vs. study architecture only):

| Component / project | License | Clean-room verdict |
|---|---|---|
| pgvector | **PostgreSQL License** (permissive, OSI-approved, more permissive than Apache-2.0) | **REUSE OK** — infra dependency; GitHub's `NOASSERTION` label is a detector failure, not a real concern |
| `rmcp`, `axum`, `sqlx`, `tokio`, `serde`, `fsrs-rs` | Apache-2.0 / MIT | **REUSE OK** |
| mem0, Graphiti, cognee, Letta, MemoryOS, txtai, memvid, agentmemory (rohitg00), GateMem | Apache-2.0 / MIT (GateMem) | study + copy patterns OK (attribute) |
| OpenClaw, Hermes Agent, Beads, gbrain, Superpowers, BMAD | MIT (OpenClaw LICENSE © OpenClaw Foundation — GitHub NOASSERTION is a detector failure) | study + copy patterns OK (attribute); harness-layer rows in `13` §1.4 |
| Hindsight, TencentDB-Agent-Memory, A-MEM, Memary | MIT | **REUSE OK** (MIT lacks a patent grant — mild IP note) |
| **`campfirein/byterover-cli`** | **Elastic License 2.0** | **LANDMINE — study only**; cannot copy code, cannot host as a service |
| **Smithery CLI** | **AGPL-3.0** | **LANDMINE — cannot vendor** |
| **`volcengine/OpenViking`** | **AGPL-3.0** | **LANDMINE — study only** (ByteDance filesystem-paradigm context DB, 26k★) |
| **`plastic-labs/honcho`** | **AGPL-3.0** | **LANDMINE — study only** (BEAM competitor) |
| MemPalace, supermemory | MIT | REUSE OK (MIT lacks a patent grant — mild IP note); verify per `13` §1.2 before any reuse |
| **Zep product** (vs. Graphiti engine) | closed-source SaaS | only the Apache-2.0 Graphiti engine is reusable |

Rule: copying code from an ELv2/AGPL/SSPL/closed project is forbidden; architecture study is always allowed. Re-verify a license before any code reuse — projects relicense.

### D-2026-07-30 — Reuse policy is license-governed, not category-governed

**Decision (owner):** rescind `13` §0's blanket "copy patterns, not code". Code
reuse is permitted from MIT / Apache-2.0 / BSD / PostgreSQL-licensed projects
with attribution, including from competitors; AGPL / GPL / LGPL / SSPL / BUSL /
ELv2 / CC-BY-NC remain forbidden to vendor (study only). Competitor *products*
remain excluded as **runtime dependencies** — reusing a competitor's Apache-2.0
source is reuse; depending on their hosted service is a dependency, and MemPhant
must stand up without anyone else's service running.

**Why:** `13` §0 was stricter than this register's own §8, which already carries
per-project REUSE-OK verdicts. The stricter line bought no legal protection and
was blocking adoption of permissively licensed work at a moment when the
measured accuracy gaps are exactly the kind that published, permissively
licensed implementations address.

**Obligations attached:** record source + license + exact commit/revision at the
point of reuse; carry the required NOTICE; verify from the LICENSE file rather
than a README badge or a cached belief; and record **model-weights licenses
separately from repository licenses** — a permissive repo with non-commercial
weights is a trap that this register must not paper over.

**Not changed:** every measurement rule. Reuse does not lower the promotion bar
— adopted code still earns its default through the same paired, preregistered,
same-lattice evidence, and a technique's published gain is never our number.

### D-2026-07-30b — Never license-blocked: tiered independent implementation

**Decision (owner):** a restrictive license on someone else's implementation
never blocks MemPhant from having the capability. If we cannot use the code, we
reimplement the behavior independently — from papers, docs, blogs, observable
behavior, or a strict clean-room reading of the source.

**Why this is mostly free:** copyright protects *expression*, not ideas,
algorithms, or methods. BM25, RRF, TM2C2, reciprocal-rank variants, compaction,
interleave fusion and the rest are published techniques. Implementing them from
their published description is ordinary independent work, not a workaround, and
carries no licence obligation from any repository that also implements them. In
almost every case we never needed the restricted repo at all.

**Protocol — pick the lowest tier that suffices:**

- **Tier 0 — published technique.** A paper, spec, or docs page describes it.
  Implement directly, cite the source. No ceremony. This covers the overwhelming
  majority of our cases, including every AGPL BM25 implementation we rejected.
- **Tier 1 — observable behavior.** No paper, but behavior is visible through
  docs, an API, or outputs. Write a functional description from observation, then
  implement from that description.
- **Tier 2 — strict clean room.** Only when the behavior genuinely cannot be
  derived otherwise. Two separated roles: a **reader** who may consult the
  restricted source and writes a functional specification containing **no code
  and no expressive detail** (no identifiers, no structure, no comments, no
  ordering that only makes sense as transliteration), and an **implementer** who
  has never seen the source and works solely from that specification. Record both
  roles, the specification, and the fact that the implementer was unexposed.

**Hard limits — state them rather than pretend the principle dissolves them:**

- **Model weights cannot be clean-roomed.** A CC-BY-NC or RAIL-licensed model
  (Jina rerankers, SFR-Embedding-Code, Qodo-Embed) is not reimplementable; the
  artifact *is* the licensed thing. The only routes are a permissively licensed
  alternative or training our own. Do not describe either as clean room.
- **Datasets cannot be clean-roomed either**, but their *methodology* is free:
  PrefEval's data is CC-BY-NC and unusable, while building our own bank to the
  same published design — which is what Track U is — is unrestricted.
- **Clean room defends against copyright, not patents.** It provides no patent
  defence. Low practical risk for retrieval scoring, but it is not zero and the
  principle does not make it zero.
- **Never** copy-paste, transliterate structure, or have one agent read
  restricted source and then write the implementation "from memory". That is the
  pattern that creates both legal exposure and a discoverable evidence trail,
  and it is precisely what the tiers exist to avoid.

**Operational note for subagents:** an agent that has read restricted source is
exposed for that component and must not implement it. Dispatch reading and
implementing to different agents, and say so in the brief.

**Unchanged:** permissive reuse (D-2026-07-30) is still preferred over
reimplementation when available — it is cheaper and carries attribution rather
than risk. And an independently implemented technique still earns its default
through the same paired, preregistered evidence; the published gain is never our
number.
