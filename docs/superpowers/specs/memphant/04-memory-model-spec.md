# MemPhant - Brain-Inspired Memory Model Spec

## 0. The Rule

Brain-inspired means separate policies, not brain-themed names.

Each memory kind must define:

- write policy
- promotion policy
- trust policy
- retrieval policy
- decay policy
- eval metric

If two kinds have the same policy, they should be one kind.

### 0.1 Why Five, Not Three (taxonomy defense)

A skeptic reads five kinds and asks "isn't this three kinds plus cosplay?" The crisp answer, so the debate never reopens:

- **`belief` is not redundant with `semantic`.** Belief is *pre-promotion* knowledge: low-trust, confidence-scored, expiring-by-default, and explicitly barred from high-risk tool arguments. Semantic is *post-corroboration* knowledge: validity-window-aware, citable as fact. They differ on **every** policy-matrix column (write, promotion, trust, decay, risk), not just lifecycle state — collapsing them would force one row to carry two contradictory decay and trust policies. The `candidate`→`active` lifecycle state (§7.1) tracks *within-kind* maturity; the belief/semantic split tracks *trust class*. Both axes are load-bearing.
- **`resource` is a kind, not just a storage tier, because its policy row is about *access and licensing*** (ACL, MIME, extractor lifecycle, leakage/licensing risk), which no other kind carries. It is a memory-kind-tagged pointer with its own retrieval-by-ACL and blob-lifecycle policy.

The justification is the **policy matrix (§1.1)**, never the brain analogy. If a future kind cannot fill a distinct policy row, it is rejected (invariant: "if a memory cannot name its policy row, it should not be stored").

## 1. The Five Memory Kinds

| Kind | Brain analog | Engineering role |
|---|---|---|
| Episodic | Hippocampus | Fast, lossless capture of specific events. |
| Semantic | Neocortex | Consolidated facts with evidence and validity windows. |
| Procedural | Basal ganglia/cerebellum | Skills, strategies, failure playbooks, deterministic fast paths. |
| Observation/Belief | Prefrontal integration | Provisional models of the world, confidence-scored and revisable. |
| Resource | External artifacts | Files, blobs, screenshots, DOM, traces, repo artifacts, URLs. |

The analogs justify separation of policy. They do not justify claims of scientific superiority. CLS/sleep-consolidation is a useful architecture metaphor: fast raw capture teaches slower consolidated memory in background jobs. This is now a **shipped, named 2026 production pattern** — Letta's two-agent "sleep-time compute" runs a consolidation agent during idle time (<https://www.letta.com/blog/sleep-time-compute>) — so MemPhant's consolidation cycle (§9) is grounded in current practice, not biology hand-waving.

**Where each kind maps in the 2026 field** (so the taxonomy is checked against winning systems, not asserted): episodic ≈ MemMachine's ground-truth-preserving store (arxiv 2604.04853); semantic-with-validity-windows ≈ Zep/Graphiti's bitemporal knowledge graph (invalidate-don't-delete); procedural ≈ ReasoningBank's strategy distillation from successes *and* failures (arxiv 2509.25140); belief ≈ the low-trust working tier that systems like MemoryOS keep hot/warm before promotion. Each MemPhant kind has a real analog that a top-of-leaderboard system treats as a first-class concern.

## 1.1 Policy Matrix

| Kind | Write path | Promotion | Retrieval default | Decay | Main risk |
|---|---|---|---|---|---|
| Episodic | append raw episode/resource first | never promoted away; derived rows cite it | exact/lexical/vector/temporal | recency and reinforcement decay | storage bloat |
| Semantic | consolidated fact with evidence | corroboration or high-trust source | precise scoped fact recall | validity-window aware | stale facts |
| Procedural | candidate strategy/failure pattern | replay or deterministic validation | explicit task/skill contexts | high stability after validation | unsafe shortcut |
| Belief | provisional observation/opinion | confidence + corroboration threshold | low-trust unless requested | aggressive unless reinforced | poisoning |
| Resource | pointer/blob/chunk | extractor success creates chunks/units | by ACL and evidence need | pointer validity and lifecycle | leakage/licensing |

If a memory cannot name its policy row, it should not be stored.

**`preference` (§13.2a) is MINTED** (2026-07-31, `20260731_006_preference_memory_kind`, classified **breaking** because the frozen kind enum is `25` §11c's closed-Rust-reader exception, so `schema_compat_revision` moved). The enum is now the **six** variants at `crates/memphant-types/src/lib.rs:968`. **`working_state` (§13.2b) remains PROPOSED and unminted** — its policy row is written there and passes the §0.1 admission test, but no enum variant exists. `knowledge` is **not** a proposed kind — it is the router-arm name for `semantic` (§13.2c), because it fills no distinct policy row.

## 2. Episodic Memory

Purpose: preserve ground truth.

Rules:

- Store before extraction.
- Content-address large blobs.
- Keep source trust and actor identity.
- Derived memories cite episodes.
- Default retrieval decays with age and low reinforcement.
- Deletion invalidates derived indexes.

Primary metric:

- answer-bearing episode recall@k
- citation validity
- storage/cost per retained episode

### 2.3 Episodic Near-Dedup

Content-addressing dedups *identical bytes*. Real agent episodes are near-identical, not identical — the same command at a different timestamp, the same error on retry. Without a dedup policy, 50 "npm install failed" episodes each spawn embeddings *and* inflate the DSR `reinforcement_count` signal (§8), so a transient failure looks like a strongly reinforced fact. This is distinct from derived-unit dedup (`dedupe_memory_units`, `14` §3), which operates downstream.

- Each episode carries a `dedup_key = hash(subject + source_kind + normalized_content)` (normalization strips timestamps, run IDs, volatile paths).
- A re-observation with a matching `dedup_key` collapses into the existing episode: `observation_count += 1`, `last_observed_at` updated, `first_observed_at` preserved, the new raw blob retained only if its hash differs.
- `reinforcement_count` for decay (§8) derives from *distinct* observations, not raw re-arrivals.

`dedup_key` and `observation_count` are **frozen now** (`00-relations-graph` §2) — retrofitting dedup onto an un-keyed episode store is a backfill migration.

**A fuzzy second pass catches what normalization misses.** Exact-hash on normalized content is the durable, audit-grade primitive, but it only collapses re-observations that normalize identically. An empirical study (arXiv:2605.09611) found exact dedup caught **5.81%** of duplicates on real prompts where MinHash-LSH caught **31.32%** — "complementary, not competitive." So a `reflect`-time second pass (MinHash-LSH or embedding near-dup, e.g. Milvus's native MinHash-LSH index) collapses paraphrastic near-dups the hash misses, incrementing `observation_count` without a new episode. The exact-hash `dedup_key` stays the source of truth; the fuzzy pass is a background-loop enhancement, never on the capture hot path. (Caveat: this is proven for prompt/training-corpus dedup; its effect on *agent-memory* recall is unmeasured — gate it behind an ablation on a MemPhant target.)

**Idempotency is on semantic identity, and re-add is non-destructive.** A retry or replayed event with a matching `dedup_key` **collapses into the existing episode** (`observation_count += 1`); it must **never** trigger a delete of the prior memory — the Mem0 #1674 failure ("adding the same memory twice deletes the old one") is exactly an LLM-driven re-add mishandled as a destructive op. MemPhant's collapse is deterministic (the keyed upsert, not an LLM decision), so an at-least-once producer is safe. And dedup keys off content, **never off a backend/config name** (the Graphiti #875 failure, where a non-default DB name silently broke dedup and duplicated every node) — the `dedup_key` derivation is config-independent by construction.

### 2.4 Retention Tiers (bounded ground truth)

Invariant #1 keeps raw episodes as *recoverable* ground truth — but unbounded raw retention with full derived-index fan-out is a cost liability at BEAM 10M-token scale and for a continuously-streaming customer like Syndai. "Storage bloat" is the episodic main risk (§1.1) and this is its mitigation. Retention is a lifecycle tier, **separate from `forget`** (which is user/legal-driven deletion, §10):

| Tier | What is resident | Trigger to enter |
|---|---|---|
| `hot` | raw blob + all derived units/embeddings/edges in Postgres | default on capture |
| `warm` | raw blob in object store, derived units kept, embeddings kept | low recall frequency + age (DSR retrievability below threshold) |
| `cold` | raw blob compressed in object store; **derived embeddings dropped, units kept as stubs**; re-derivable on demand | sustained zero recall past a tenant-configurable window |

- A `tier_episode` background job (`14` §3) demotes by policy and is fully reversible — a cold episode re-promotes to `warm`/`hot` on the next recall that touches it, re-deriving embeddings from the recoverable raw blob.
- Cold-tiering **never** drops the raw episode or its citation path; it only drops the *Postgres-resident derived index* whose cost is the problem. This is why invariant #1 reads "recoverable," not "always hot."
- A provider sunset (an embedding model deprecated mid-corpus) is survivable precisely because re-embedding runs offline from the raw episode (`14` §10) — a direct payoff of keeping ground truth recoverable.

`episode.retention_tier` is **frozen now**. Storage-growth-per-tenant is a first-class SLI with an alarm threshold (`22` §SLIs).

> **UNBUILT (verified 2026-07-30).** This section froze the vocabulary and named the job; it never said who writes the column or on what predicate. As a result `episode.retention_tier` (`20260703_001_wsa_bootstrap.sql:278`) has **0 readers and 0 writers** in `crates/` and every episode is `'hot'` forever, and `tier_episode` **does not exist in code**. The transition table, writers, invariants, and conformance tests are in **§13.3**; the demotion-vs-deletion distinction is **§13.4**.

## 3. Semantic Memory

Purpose: durable facts.

Rules:

- Facts require evidence.
- Facts are bitemporal: observed time and transaction time.
- Conflicts create supersession/contradiction edges, not silent overwrite.
- Promotion from belief to semantic requires confidence and source rules.

Primary metric:

- factual precision
- stale fact suppression
- contradiction **detection** precision/recall (did we *find* the conflict)
- contradiction **resolution** accuracy (given a detected conflict, did the right fact win)

### 3.1 Contradiction Detection Contract

Invariant #6 ("conflicts create edges, never silent overwrite") is architecturally elegant but it **moves** the hard problem rather than solving it. If detection is weak you do not get silent overwrite — you get **silent accumulation**: two contradictory `active` semantic units, both citable, both passing retrieval, and the model picks one at random. That is worse than overwrite because the trace looks healthy. (This is the failure mode documented in the field's leading systems — e.g. extraction pipelines that store both "my name is X" and "my name is Y" because dedup only catches exact duplicates.) So contradiction detection is a **named contract**, not a background-job side effect:

- **Trigger** — a candidate `contradicts` edge is proposed when a new semantic unit shares a **subject key** with an existing active unit, their embeddings are within a proximity threshold, **and** their `valid_*` windows overlap. Subject key = `(scope_id, normalized_subject, predicate)` derived at extraction; it is what makes "callback token version" collide regardless of surface wording.
- **Resolution** — a detected conflict routes to the resolution policy: higher-trust source wins; on equal trust, the fact with the later represented-world `valid_from` is current — **but the authoritative ordering is the DB-assigned, monotonic `transaction_from` (DB clock / HLC), NEVER a writer-asserted wall-clock**. `valid_from` is server-stamped at "true now"; only an explicit retroactive correction supplies a past `valid_from`, and even then the winner among concurrent generations is decided by `transaction_from`. This closes the classic skew hazard where two concurrent writers' unsynchronized clocks produce a non-deterministic winner and a retroactive correction silently loses to a stale generation (DDIA/Lamport; XTDB serializes writes through a single writer for exactly this; TOKI arXiv:2606.06240). The supersession closes the older generation per §7.3a (never an in-place `valid_to` edit); unresolved conflicts stay as **dual-active with a `contradicts` edge surfaced in recall as a caveat** (never silently dropped).
- **Targets** — detection precision/recall is reported separately from resolution accuracy (a system can resolve perfectly and still miss half the conflicts). Both are tracked against the BEAM "knowledge-update" / "contradiction-resolution" ability categories (`12` §10).
- **Eval honesty** — golden cases for this path must **not** pre-annotate the supersession (real episodes do not arrive labeled with their own conflict). At least one golden family seeds two plain episodes and asserts the system *derives* the contradiction edge itself (`05` §4.2).
- **A cheap LLM judge confirms the ambiguous residual** — embedding cosine is *polarity-blind*: contradictory pairs score as similar as (or more than) agreeing pairs, because retrieval embeddings encode topical overlap, not negation (SparseCL arXiv:2406.10746; biomedical-embeddings arXiv:2110.15708). So proximity-only detection **over-flags**. The validated SOTA pattern is exactly MemPhant's split done one step further: embedding+temporal+subject-key for *candidate selection*, then an LLM *decides* — Zep invalidates LLM-confirmed temporally-overlapping edges (arXiv:2501.13956); Mem0 lets an LLM pick ADD/UPDATE/DELETE/NOOP (arXiv:2504.19413). MemPhant keeps the deterministic gate as the front line (subject-key + valid-overlap resolves the clear majority at zero LLM cost) and invokes a **cheap (Haiku-class) LLM judge only on the ambiguous residual** — proximity suggests conflict but subject-key match is uncertain. This runs in the background `reflect` loop (§9), never the recall hot path, so the cost is acceptable. Track `contradiction_detection_method` (`deterministic` vs `llm_assisted`) and ablate; if deterministic precision holds above target, the judge stays dormant. (A trained NLI classifier is *not* the answer — it trades recall for much lower precision; the cheap-LLM-judge-on-candidates pattern is what the field converges on.) **The judge's decision is a keyed write-time audit, not a transient call:** when the LLM judge adjudicates, its model id/version + the losing fact + the resolution reason are written to a keyed audit row (a `trust_event`, `06` §2.2) — a contradiction-resolution LLM-judge on the write path is **replay-inconsistent unless its decision is keyed-logged** (TOKI arXiv:2606.06240: contradiction resolution is write-time concurrency control with a declared isolation level; the per-`subject_key` lease at `02` §6.2 supplies that isolation). Resolution is therefore a write-time *typed contract*, not a loose scoring recipe.

### 3.2 Retroactive Validity Correction (resolved by append-only generations)

Late-arriving evidence that changes a *past* validity window ("the policy changed May 1, not June 1" arriving in July) is **mechanically free** under the existing temporal fields once the §7.3a append-only invariant holds: it is just another belief-generation. The `correct` event **closes** the open generation (`transaction_to = now`) and **INSERTs** a new generation carrying the corrected past `valid_from`/`valid_to` — it never re-opens or edits a historical range in place, so transaction-time audit-replay is preserved by construction (Fowler bitemporal-history; SQL:2011 system-versioning; XTDB). The only residuals are *surfacing*, not mechanism: optional `valid_from`/`valid_to` args on the `correct` verb (`08` §4.2), and the note that already-emitted citations are point-in-belief-time artifacts (reproducible with the trace's independent `transaction_as_of` and `valid_at`, never silently rewritten). Rung-5 traces (`27`) gate *marketing* the capability, not its existence.

### 3.3 Subject-Key Derivation (load-bearing for §3.1)

`subject_key` is what makes "the callback token version" collide regardless of surface wording, so its derivation is a **contract, not a free LLM choice** — two extractions of the same fact must produce the *same* key or contradiction detection silently fails into accumulation (§3.1).

- **Shape:** `subject_key = (scope_id, normalized_subject, predicate)`. The LLM extractor proposes `(subject, predicate)`; the **engine canonicalizes** them — never stored raw.
- **Canonicalization (deterministic, post-LLM):** lowercase, strip articles/possessives, resolve aliases via an in-scope alias table (`same_subject` edges + a per-tenant alias map), collapse to a singular canonical form — so "our refund window", "the refund window", "refund window" all key to `(scope, refund_window, duration)`.
- **Predicates are a scoped controlled set, not free text.** A novel predicate that is a synonym of an existing one (`is`/`equals`/`set_to`) folds to the canonical predicate; this is what stops "X is 30 days" and "X = 14 days" from missing each other.
- **One canonicalizer at write and at probe.** The same code path sets the key at write and resolves it at the §3.1 probe (`WHERE subject_key = …`), so a divergence is a detection *bug*, caught by the `05` §4.2 derived-contradiction golden.
- `subject_key` is **nullable** and meaningful only for `semantic`/`belief` (the kinds that contradict); episodic/resource units leave it null (the partial index in §7 already assumes this).
- **A canonicalizer *logic* change re-keys all contradiction detection** — so it is versioned through `compiler_version` (`08` §7) and applied as an **offline rebuild** that re-canonicalizes `subject_key` from the recoverable raw episodes (§2.4), never an in-place per-row guess. The per-tenant alias *data* re-keys the same cheap way; only the canonicalizer *code* change needs the version bump.

### 3.4 Supersession Single-Apply (the resolution race, made safe)

§3.1 resolution sets the older unit's `valid_to` and may flip `state` to `superseded`. Under at-least-once, parallel `reflect`, this is a classic double-decide race: **two workers both read two units as `active`, both decide to supersede, both write `valid_to`/`state`** — at best a redundant write, at worst thrash (each picks a different loser, or one re-opens what the other closed) and a duplicate `supersedes` edge attempt. The `UNIQUE (tenant, src, dst, kind)` edge constraint (§7) makes the *edge* a no-op upsert, but the **`valid_to`/`state` mutation on `memory_unit` is the unguarded RMW** the edge-uniqueness claim (`02` §6.1, now corrected) wrongly assumed was covered.

Single-apply comes from two things already specified, made explicit here:

- **Decide inside the §6.2 subject-lease.** Both contradicting units share one `subject_key` (that is *why* §3.1 fired), so both candidate supersessions fall under the **same** `pg_try_advisory_xact_lock((tenant, subject_key))`. Only one `reflect` holds it at a time; the second worker skips and redelivers. So the *decision* runs once per subject, not concurrently — no two workers race the same `valid_to`.
- **Deterministic resolution makes a redelivered decision a no-op.** Resolution is a **pure function of the two units' immutable fields** — higher-trust source wins; on equal trust, **newer `valid_from` supersedes the older** (§3.1). Because the winner is a deterministic function of `(trust, valid_from)` and not of *who ran first*, a re-run after redelivery re-derives the **same** loser. The close is **append-only and guarded** (§7.3a — transaction-time never mutates a row's `valid_*` in place): `UPDATE … SET transaction_to = now(), state = 'superseded' WHERE id = :loser AND state = 'active' AND transaction_to IS NULL` (closing the loser's open generation), and the resolved/new validity lands in the **inserted** winner generation — both in the one `MemoryStore` transaction (`03` §4). Once the loser is closed, the `WHERE state='active' AND transaction_to IS NULL` makes the redelivered write affect zero rows. Nothing is moved twice, and the resolution can never thrash because it is deterministic-winner-wins, not last-writer-wins. *(This corrects the prior wording, which set the loser's `valid_to` in place — an in-place valid-time mutation that would break dual-axis audit replay; R57.)*

Dual-active-with-`contradicts`-edge (the unresolved case, §3.1) is likewise single-applied: the edge is the `(tenant, src, dst, kind)` no-op upsert, and no `valid_to` moves. The supersession path therefore needs **no new mechanism** — it is the §6.2 lease + the §3.1 deterministic rule + a state-guarded write, which is why this is a clarification of an under-specified contract, not a new subsystem.

## 4. Procedural Memory

Purpose: repeatable ways of doing work.

Rules:

- A procedure starts as a candidate strategy.
- Promotion requires replay or deterministic validation.
- A failed strategy can be retained as a failure pattern.
- Procedures are scoped; siblings do not inherit raw context.

### 4.1 What a `procedure` unit stores

A `procedure` is `kind='procedural'` in `memory_unit`; its structured payload lives in the shared `payload jsonb` adjunct (§7 — not new columns). Fields (the `{title, intent, steps}` triple is the public ReasoningBank-shaped schema, arxiv 2509.25140; the rest are MemPhant's gating additions):

| Field | Meaning |
|---|---|
| `title` / `intent` | strategy name (retrieval key) + the scope-scoped task it claims to help (never global) |
| `preconditions` | observable conditions under which it applies — matched at recall, then it competes on rank |
| `steps` | ordered action *sketch* — strategy, **not** a replayable script and **no exact tool args** (that would be a skill compiler + a poisoning vector) |
| `signal_kind` | `success` \| `failure` — the discriminator (one table, one kind; differentiated here, not in storage) |
| `evidence_refs` | episode IDs distilled into this unit |
| `validation` | `{status, method, last_replayed_at, trials, wins}` |

### 4.2 Candidate → validated (the gate)

A candidate is **not recallable as trusted context** (§7.1). It crosses to `validated` by **replay** (`method='replay'`, ≥ threshold wins over trials in the eval harness) or **deterministic validation** (the effect is statically checkable) — **never by corroboration count.** Replay is **adversarial**: it proves "it ran" *and* asserts no high-risk/destructive step (force-push, validation-skip, secret exfiltration), because a poisoned procedure can pass naive replay (MemoryGraft, arxiv 2512.16962). Procedural recall applies the `06` §4.2 high-risk-arg label to the *steps*, not just the unit. A procedure many agents agree on but that fails replay is still wrong: procedures gate on *outcome*, beliefs on *source independence* (§5). A `failure` unit is validated when its failure is *reproduced/confirmed* — true when the bad path reliably fails — then it lives only to *suppress* a path, never to drive one.

> **The write side of this gate is UNBUILT (verified 2026-07-30).** `UnitState::Validated` is *required* on the read path (`crates/memphant-core/src/lib.rs:8362`, eligibility predicates `lib.rs:3284-3285` / `store.rs:3724`, `:3765`), but **no write path in `crates/*/src/` ever transitions a unit into `Validated`** — so nothing can pass a gate that is nonetheless enforced. The write-side contract is `procedural_arm` in §13.1; the implementation is W3.5.

### 4.3 Success vs failure differ in retrieval and decay

| Axis | `success` | `failure` |
|---|---|---|
| Retrieval role | injected as a *recommended* path when preconditions match | injected as an *avoid/warning* label when the current plan resembles the failed path; never proposed as the action |
| Decay (§8) | high stability after successful replay | medium; retired when `obsolescence` edges fire, not by time |
| Eval metric | task-success delta, step-count reduction | **failure-recurrence reduction** (did the warning prevent the repeat?) |

Guardrails: No CRDT/Yjs. No full skill compiler. `steps` is a strategy sketch, validated by replay-in-harness, never stored exact args.

**"Executable memory," answered (Round 10 — so this never relitigates).** The recurring external ask — store rules ("IF X THEN respond concisely") and auto-triggered workflows ("when deploy fails: logs → docker → DNS") so "memory becomes reusable behavior" — is served by a named safe subset, and the unsafe remainder is rejected: (1) the workflow example IS a `procedure` unit — `preconditions` matched at recall, injected as a *recommendation*, gated by adversarial replay (§4.2); (2) the rule example IS a `trusted_user` semantic fact with a preference-class predicate that the RUNTIME chooses to apply; (3) a "rules/evaluate" endpoint that only labels IS `recall(kinds:[procedural])` — a synonym verb; (4) runtime triggering rides the consolidation-event outbox (`20` §3) the runtime subscribes to and acts on with its own judgment. What stays rejected is engine-side auto-execution and a stored condition/action DSL: auto-execution converts memory poisoning into persistent code execution (a poisoned procedure passes naive replay — MemoryGraft; query-only poison has clean provenance — MINJA), and the runtime's judgment layer between recall and action is the last defense the honest-residual analysis (`06` §4.2) depends on. Memory is evidence, never control flow (invariant #4, `26` §4/§7).

Primary metric:

- task success delta
- step count reduction
- failure recurrence reduction

## 5. Observation/Belief Memory

Purpose: provisional models.

Rules:

- Low-trust and uncorroborated inputs land here first.
- Beliefs carry confidence, source trust, and expiration policy.
- Beliefs never drive high-risk tool parameters without corroboration.
- Promotion to semantic memory is explicit.

#### Corroboration requires *independence*, not just count

This is the one word the poisoning defense (invariant #3) hangs on, and the naive reading is exploitable. "Corroborated" must **not** mean "≥N supporting observations" — an attacker controlling several low-trust channels (multiple web pages, repeated tool outputs) can manufacture count-corroboration and farm a false belief into high-trust semantic memory, with a clean trace. The promotion gate therefore requires the supporting evidence to come from **distinct `actor_id` AND distinct `source_kind`/origin**, not merely distinct rows:

- belief→semantic promotion needs ≥2 *independent* sources (different actor and different origin class), or one explicitly high-trust source.
- N mutually-reinforcing observations from a single origin count as **one** source for corroboration, however many rows they create.
- this is enforced in the `reflect` consolidation contract (§9) and exercised by the `corroboration-farming / Sybil poisoning` security suite (`05` §10, `06` §7).

**Cold-start exception (new users — the gate must not under-promote).** A new user's first facts are single-source by construction (no corpus to corroborate against), so a strict ≥2-source gate would strand every early fact at `belief` and the assistant would feel amnesiac. The escape is the existing "one explicitly high-trust source" clause: a **direct first-party user assertion** (`trusted_user`, e.g. "my name is X", "I prefer metric units") promotes on a single source — a user stating a fact about themselves *is* the authority, and there is no independent source that could ever corroborate it. The independence gate is for *low-trust* claims (web/tool/agent output), not for the user telling you about themselves. This is what makes recall useful from session 2, not session 20 (`01` §0.1 new-user edge cases).

### 5.1 Confidence Dynamics (the mechanics, not "confidence-scored")

`memory_unit.confidence ∈ [0,1]` is **engine-owned, never the extractor's raw self-report** — LLM-estimated probabilities are premature-overconfident and history-blind (BDI survey arxiv 2510.20641). The extractor proposes an initial prior by trust class; `reflect` (§9) moves it by rule:

| Event | Update | Bound |
|---|---|---|
| initial write | prior by trust class (`06` §2.2 multipliers: `web_content`→0.3, `agent_output`→0.4, …) | — |
| independent (dis)confirmation | confirm `c ← c + α(1−c)`; disconfirm `c ← c − α·c` (one origin = one event, §5) | clamp [0.05, 0.95] — beliefs never reach certainty; that is semantic's job |
| time without confirmation | passive decay toward the source-trust prior (regress to prior, not to 0) | — |

### 5.1a Confidence is recomputed, never incrementally mutated (at-least-once correctness)

The update rules above describe the *effect* of one confirmation, **not an in-place `UPDATE confidence = confidence + α(1−confidence)`.** Written as a read-modify-write (RMW), `c ← c + α(1−c)` **double-applies under at-least-once `reflect` delivery** (`02` §3.0): a redelivered confirm event (pgmq vt expiry, Temporal activity retry — both at-least-once) re-runs the increment and the belief is over-confident from a duplicate it should have ignored. The §6.1/§6.2 subject-lease serializes *concurrent* writers but does **not** make a *redelivered* event a no-op — two serialized applications of the same event still double-count. Idempotency is a separate, mandatory property.

**The fix (event-sourced recompute, the KISS choice): confidence is a pure function of the deduped observation set, recomputed — never a mutable accumulator.**

- Each (dis)confirmation is an **immutable row** in a `belief_observation` ledger keyed by a stable `source_event_id` (the originating `episode_id` + emitter), with a `UNIQUE (tenant_id, memory_unit_id, source_event_id)` constraint (tenant_id leads — the ledger is `PARTITION BY HASH (tenant_id)` like every core table, §7.0). A redelivered event is an `ON CONFLICT DO NOTHING` insert — a **structural no-op**, not a hoped-for one. This is the standard "exactly-once *effects* on at-least-once delivery" pattern: dedup the event, then the effect applies once.
- `confidence` on `memory_unit` becomes a **derived/cached projection**: `reflect` folds the *distinct* (per the UNIQUE) observations through the prior + `c ← c + α(1−c)` / `c ← c − α·c` rules **in `source_event_id`-deterministic order** and writes the result. Recompute is a pure fold over the unit's own ledger, so it is **idempotent by construction** — running it once, twice, or after a crash yields the same `confidence`. The "one origin = one event" independence rule (§5) is enforced at *ledger insert* (collapse same-origin rows to one observation), so corroboration-farming is contained at the same boundary.
- **Bounded, not O(all history).** The fold reads only this unit's observation rows (small — a belief accumulates a handful of confirmations, not the corpus), filtered `WHERE memory_unit_id = :u`. It is **not** a scan of all events ever. A unit with pathologically many observations is exactly a strongly-reinforced fact heading for promotion (§5.2); the fold is still per-unit-bounded. If a hot belief's ledger ever grows unbounded, a **periodic checkpoint** (fold-to-date → `confidence_checkpoint{value, last_event_id}`, then fold only events after the checkpoint) bounds it — the same checkpoint shape as §3.4/§8.2.

This **removes the RMW entirely**: there is no `confidence = confidence + …` statement anywhere, so there is nothing to double-apply. The dedup table is the floor even if a future design prefers an incremental cache; recompute-from-ledger is the recommended primary because it is the one design where a redelivered, reordered, or replayed event is provably harmless.

**Fold-shape ablation arm (R91).** The Beta-posterior form `conf = α/(α+β)·q` is a principled alternative *fold shape* — computed over the same deduped, independence-collapsed ledger (never as raw mutable counters, which double-apply under redelivery and reward repetition). It ships as a `05` §9 ablation arm against the `c ← c + α(1−c)` fold; same ledger, same clamps, zero schema change.

### 5.2 Promote / demote / expire

Three exits, decided in the `reflect` corroboration stage (§9):

- **Promote → semantic** when confidence ≥ threshold **AND** the §5 independent-source gate holds. A kind change + `derived_from` edge from the belief precursor — never an in-place edit; the belief stays citable as the precursor.
- **Demote / hold** when confidence rises but independence fails (corroboration-farming): stays `belief`, **barred from semantic and from high-risk tool args.** This makes the §0.1 "high-confidence belief is still not a fact" claim concrete: confidence ≠ trust class.
- **Expire** when confidence decays below the floor with no reinforcement → `state='expired'`. Expiry (not decay) is the default fate — unlike a semantic fact whose decay lowers *priority*, an expired belief leaves default recall entirely.

Confidence and DSR-retrievability (§8) are **orthogonal**: a belief can be high-retrievability (recently reinforced) yet low-confidence (single low-trust origin), and must be surfaced *with* its label, never silently dropped or trusted.

Primary metric:

- correct promotion rate
- false belief suppression
- poisoning containment (incl. **corroboration-farming resistance** — independent-source gate holds)

## 6. Resource Memory

Purpose: point to external evidence.

Rules:

- Store pointers and hashes, not everything inline.
- Each resource carries MIME/type, ACL, tenant, scope, trust, and extractor status.
- Resource chunks are embeddable; resource identity remains separate.

### 6.1 Extractor State Machine + Chunk Lifecycle

A `resource` is a *pointer* (`uri`, `content_hash`, `acl`, `mime`); it becomes recallable only after extraction produces **chunks** embedded as `resource`-kind units. `resource.extractor_state` (`03` §5.1) is the machine:

```text
registered  -- pointer + hash + ACL stored; nothing embeddable yet
  -> fetching   -- server-side fetch through the SSRF floor (06 §3.1)
  -> extracting -- mime-specific extractor (pdf/text/dom/image/trace)
  -> chunked    -- chunks materialized as candidate resource units
  -> embedded   -- chunks embedded per active embedding_profile -> recallable
  -> failed     -- extractor error; pointer retained, recall by metadata only
  -> stale      -- source content_hash changed; chunks superseded, re-extract queued
```

- **Identity is separate from chunks:** one `resource` row, N chunk units (`derived_from` edge). Forget cascades resource→chunks; forgetting a chunk does not delete the pointer. A chunk's `subject_key` is NULL, so chunks never enter contradiction/decay (excluded from the §7 probe index by construction).
- **`resource.acl` is a typed *narrowing* gate, reusing existing primitives (no parallel ACL engine):** `acl: {scopes?: [scope_id], trust_floor?: <06 §2.2 class>, protected?: <06 §2.1 tag>}`. A chunk's own `scope_id` clearing `policy.scopes` is **necessary but not sufficient** — recall additionally requires the parent `resource.acl` clauses, applied **in-stage** (`03` §5.2, joined via `derived_from`), so the ACL can only *narrow*, never widen. A denied chunk lands in `dropped[]` with `reason: protected_category | below_trust_floor` (`06` §4.2). This closes the leak where a chunk-as-unit's scope passes but the parent ACL is never consulted.
- **Fetched content enters at `web_content` trust, never higher** (`06` §3.1) — extractor output is data, not instruction, even from a "trusted" URI.
- **Re-extraction is offline + idempotent:** a provider sunset or `stale` re-runs `extracting`→`embedded` from the retained blob (the resource analog of episodic cold-tier re-derivation, §2.4).
- **Lifecycle ≠ forget:** a `stale` resource still answers from old (labeled-stale) chunks until re-extraction; a `forgotten` one answers nothing. `extractor_state` distribution + time-in-`extracting` is the read on "blob lifecycle compliance" and a `consolidation_lag` contributor.
- **Optional multi-resolution summaries (L0/L1/L2)** — for large resources, store an `L0` (title+abstract, ~50 tokens) and `L1` (structural overview, ~200 tokens) alongside the full `L2` chunks (in `payload`, §7). Recall returns L0/L1 by default and loads L2 only on explicit request or when L0/L1 match scores clear a threshold — so an agent scans N resource abstracts cheaply before pulling a few in full (the OpenViking filesystem-tiering pattern, VikingMem arXiv:2605.29640; directly addresses the "resource bloat" risk and the coding-agent "scan 20 file summaries, load 3" case). Tiering is a cost/latency lever, orthogonal to `retention_tier` (which is storage-cost, §2.4) — gate behind an ablation on a MemPhant target.

Primary metric:

- resource evidence recall@k
- pointer validity
- blob lifecycle compliance

## 7. Core Tables

The conceptual entity set is `tenant`, `subject`, `actor`, `agent_node`, `scope`, `episode`, `memory_unit`, `memory_edge`, `resource`, `embedding_profile`, `embedding`, `citation`, `trust_event`, `deletion_generation`, `retrieval_trace`, `job_state`. Canonical pseudo-DDL for the load-bearing tables (column + type + constraint + index; full DDL discipline — `NOT VALID`→`VALIDATE`, concurrent index build, `set_updated_at` trigger, ≤63-char constraint names — is owned by `03` §5 and `25`). All tables live in the `memphant` schema, never `public`/`syndai`; every tenant-scoped table leads its primary index with `tenant_id`, is `PARTITION BY HASH (tenant_id)`, and carries `tenant_id` in its primary key (§7.0).

```sql
-- Ground truth. Recoverable, tiered, near-deduped.
episode (
  id              uuid NOT NULL,               -- UUIDv7, time-sortable
  tenant_id       uuid NOT NULL,
  scope_id        uuid NOT NULL,
  actor_id        uuid NOT NULL,
  agent_node_id   uuid,
  source_kind     text NOT NULL,              -- 'user'|'agent'|'tool'|'web'|'resource'|'system'
  source_trust    text NOT NULL,              -- see trust_event vocabulary
  dedup_key       text NOT NULL,              -- hash(subject + source_kind + normalized_content)
  observation_count int NOT NULL DEFAULT 1,
  retention_tier  text NOT NULL DEFAULT 'hot' CHECK (retention_tier IN ('hot','warm','cold')),
  blob_hash       text,                        -- content-addressed; raw lives in object store
  body            text,                        -- inline only while small/hot
  first_observed_at timestamptz NOT NULL,
  last_observed_at  timestamptz NOT NULL,
  transaction_from  timestamptz NOT NULL DEFAULT now(),
  deletion_generation bigint,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),                 -- tenant_id in PK: hash-partition key (§7.0)
  UNIQUE (tenant_id, scope_id, dedup_key),
  INDEX (tenant_id, scope_id, source_kind, last_observed_at),
  INDEX (tenant_id, retention_tier) WHERE retention_tier <> 'hot'
) PARTITION BY HASH (tenant_id)

-- Derived knowledge of all five kinds. Lifecycle + bitemporal + trust.
memory_unit (
  id              uuid NOT NULL,
  tenant_id       uuid NOT NULL,
  scope_id        uuid NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('episodic','semantic','procedural','belief','resource','preference')),
  state           text NOT NULL CHECK (state IN
                    ('captured','extracted','candidate','active','superseded',
                     'invalidated','deleted','quarantined','expired','validated','retired')),
  subject_key     text,                        -- (normalized_subject, predicate); drives contradiction detection
  body            text NOT NULL,
  confidence      real CHECK (confidence BETWEEN 0 AND 1),
  trust_level     text NOT NULL,
  -- bitemporal (semantic/belief): represented-world vs transaction time
  valid_from      timestamptz,
  valid_to        timestamptz,
  observed_at     timestamptz,
  transaction_from timestamptz NOT NULL DEFAULT now(),
  transaction_to   timestamptz,
  -- decay (DSR): per-unit state is (stability, difficulty); retrievability is computed
  difficulty      real CHECK (difficulty BETWEEN 0 AND 10),
  stability_days  real,
  last_reinforced_at timestamptz,
  reinforcement_count int NOT NULL DEFAULT 0,  -- distinct observations, not raw re-arrivals
  desired_retention real DEFAULT 0.9,
  last_confirmed_at timestamptz,               -- active freshness (§8.1)
  freshness_due_at  timestamptz,               -- indexed due scan for volatile facts (§8.1)
  deletion_generation bigint,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),                 -- tenant_id in PK: hash-partition key (§7.0)
  -- hot recall path: ONLY the open/current generation (§7.3a) — append-only history never on the hot path
  INDEX (tenant_id, scope_id, kind, valid_to) WHERE state = 'active' AND transaction_to IS NULL,
  INDEX (tenant_id, scope_id, subject_key) WHERE state = 'active' AND transaction_to IS NULL,  -- contradiction probe
  INDEX (tenant_id, freshness_due_at) WHERE state = 'active' AND transaction_to IS NULL AND freshness_due_at IS NOT NULL,
  -- ≤1 open generation per fact (structural): a second open INSERT fails (§7.3a)
  UNIQUE (tenant_id, subject_key) WHERE transaction_to IS NULL AND kind IN ('semantic','belief'),
  -- cold audit/history surface (transaction_as_of replay, off the hot path)
  INDEX (tenant_id, subject_key, transaction_from)
) PARTITION BY HASH (tenant_id)

-- Typed relational graph. The graph IS Postgres edges — no separate graph DB.
memory_edge (
  id          uuid NOT NULL,
  tenant_id   uuid NOT NULL,
  scope_id    uuid NOT NULL,
  src_id      uuid NOT NULL,
  dst_id      uuid NOT NULL,
  kind        text NOT NULL CHECK (kind IN
                ('supersedes','contradicts','derived_from','cites','same_subject','depends_on')),
  observed    boolean NOT NULL DEFAULT false,  -- declared vs inferred
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),                 -- tenant_id in PK: hash-partition key (§7.0)
  UNIQUE (tenant_id, src_id, dst_id, kind),
  INDEX (tenant_id, scope_id, dst_id, kind)
) PARTITION BY HASH (tenant_id)

-- One row per (unit, embedding_profile). Profile pins model+dims+index strategy.
embedding (
  memory_unit_id      uuid NOT NULL,
  embedding_profile_id uuid NOT NULL,
  tenant_id           uuid NOT NULL,
  vec                 halfvec,                 -- DIMENSIONLESS: holds mixed-dim profiles (halfvec stores ≤16000); dim enforced PER PROFILE by core at write, never a column typmod; see 02 §2.1a
  PRIMARY KEY (tenant_id, memory_unit_id, embedding_profile_id)  -- tenant_id in PK: hash-partition key (§7.0)
  -- ANN index is PER PROFILE and PARTIAL on embedding_profile_id (02 §2.1a / 25 §4); the query MUST
  -- carry `AND embedding_profile_id = $pid` or the partial index is never chosen (silent seq scan).
  -- Every active profile owns a partial index OR is `exact` — a db-lint failure otherwise.
  --   hnsw_full      : HNSW on vec  (halfvec dims <= 4000, NOT 2000)          WHERE embedding_profile_id = $pid
  --   hnsw_subvector : HNSW on subvector(vec,1,2000)::halfvec(2000) + rerank  (dims > 4000; MRL/Matryoshka models ONLY — arbitrary truncation destroys recall)
  --   hnsw_binary    : HNSW on binary_quantize(vec)::bit(D) + halfvec rerank  (scale lever; FORBIDDEN below ~1024-d — raw bit recall collapses, 02 §2.1a)
  --   exact          : no index (tiny corpus)
) PARTITION BY HASH (tenant_id)
```

`memory_unit.kind` is exactly one of `episodic`, `semantic`, `procedural`, `belief`, `resource`, `preference` — **frozen-but-extensible.** The sixth was added on 2026-07-31 by exactly the governed path this paragraph describes, so the extensibility claim is now demonstrated rather than asserted. What is frozen is the *contract that every unit has exactly one kind*, not the cardinality of the set: adding a 6th kind is a **governed additive migration** (an RFC + the `25` §11c add-enum-value path read as TEXT-with-fallback), never a forbidden rewrite. So the field's drift toward graph/temporal-KG memory (where "plain memory can be regarded as a degenerate graph", arXiv:2602.05665, and contradiction-handling is edge-invalidation over temporal validity — Graphiti/Zep, which MemPhant already expresses as `contradicts`/`supersedes` edges + the §7.3a bitemporal layer) is absorbed by adding a kind or a typed edge, not by a re-architecture — closing the "freeze a 5-value enum too early = schema regret" risk.

**Kind-specific payload (no per-kind columns).** `memory_unit` is the *shared* lifecycle/trust/bitemporal/DSR row for all five kinds — frozen, and it must not grow `procedure_*`/`resource_*` columns. Kind-specific structure lives in one `payload jsonb` on the unit (validated by a per-kind JSON schema in the Rust core), e.g. the procedure body (§4.1) and resource-chunk refs (§6.1). Recall/contradiction/decay paths use only the typed columns; `payload` is read at assembly time, so its shape can evolve behind a versioned schema without touching the frozen table.

### 7.0 Physical Partitioning (the frozen physical-layout contract)

`episode`, `memory_unit`, `memory_edge`, `embedding`, and the event ledgers (`belief_observation` §5.1a, `review_event` §8.2, `blob_ledger` `03` §5.1) are all `PARTITION BY HASH (tenant_id)` on a shared modulus `MEMPHANT_PARTITION_MODULUS`. Every hot query already prefilters `tenant_id =`, so hash-by-tenant aligns the physical layout with the one universal filter and — the load-bearing win — gives **each partition its own local HNSW index**, the documented fix for pgvector's small-tenant filtered-recall collapse (`02` §2.1b; pgvector #479) that thousands of partial indexes over one heap could not. Postgres requires the partition key in every PK/UNIQUE, so `tenant_id` leads every PK — aligned with the existing tenant-leading discipline, but a bare `WHERE id = :x` no longer guarantees a unique hit and must carry `tenant_id`.

- **Modulus is the one tunable, set once at bootstrap, then immutable** (changing it is a full re-hash/rewrite): default **64** hosted, **4–8** BYOC/modest PG, **1** = a **plain unpartitioned table** — at modulus 1 the bootstrap emits **no `PARTITION BY` clause at all** (NOT a one-partition hash, which still pays planner/metadata overhead per the PostgreSQL partitioning docs), so a single-tenant self-host pays **zero** partitioning cost. Partitioning is **opt-in** (modulus > 1 only); the `tenant_id` isolation key (RLS + leading index) stays in every deployment regardless — RLS-with-one-tenant is near-zero when the policy column is indexed and `current_setting` is `(select …)`-wrapped. Partitions (when > 1) are static DDL emitted once by the bootstrap migration (`CREATE TABLE … FOR VALUES WITH (MODULUS m, REMAINDER n)` × m); a new tenant needs **no DDL** (it hashes into an existing partition). **No pg_partman** (it automates only RANGE/LIST, not HASH) and **no Citus** (AGPL, effectively Azure-only — incompatible with Apache-2.0 self-host).
- **Rejected:** RANGE(time) (no time predicate on the hot path; fragments one tenant's vectors across N graphs → recall loss); LIST-per-tenant (thousands of partitions → planner blowup; the planner handles "up to a few thousand" partitions well only when queries prune to a few — PostgreSQL partitioning best-practices); composite hash→range (partition explosion for zero recall gain).
- **`forget` stays `DELETE WHERE tenant_id = :t` + `deletion_generation` tombstone + crypto-shred** (`06` §6.2) — hash-by-tenant does NOT make per-tenant delete `O(drop-partition)` (a hash partition co-locates many tenants). Partitioning's deletion win is blast-radius containment, not drop-speed.
- **The whale (one tenant dwarfing the rest) has a FIRST-CLASS promotion path — this is the escape hatch the immutable modulus requires.** Hash balances by tenant *count*, not *size* (ClickHouse's Postgres-SaaS guidance: "a single whale tenant can dominate one partition, which is the cue to move that tenant to dedicated infrastructure"), and PostgreSQL's own partitioning docs warn the per-customer scheme can become impractical as the tenant distribution drifts, while "re-partitioning large quantities of data can be painfully slow." Since the modulus is immutable the answer is **never re-hash — it is promote**: (1) first, hash confines the whale's graph to its partition (it cannot degrade others' recall) and that partition runs `hnsw_binary`(≥1024-d) + corpus-size-aware `ef_search` (`02` §2.1a/b); (2) when the partition exceeds its node's RAM/HNSW-build headroom (`25` §11a) or its filtered-recall SLO, **promote the whale to a dedicated single-tenant cell / store / vector engine** (`25` §7b, `02` §2.1b). This mirrors the industry ladder (Qdrant tiered-multitenancy + tenant-promotion, ~1000-dedicated-shard cap as the cue; Milvus's isolation ladder; Pinecone namespace-per-tenant). The promotion path exists from day one even if unused — without it, immutable-modulus is a trap.
- **Modulus stays in the low hundreds for plan time.** Planning time grows ~linearly with partition count (postgres.ai: ~12 ms at 1,000 partitions, ~35× a PK lookup's *execution* time), and stays cheap only because every MemPhant query prunes to one partition on `tenant_id`. Choose the modulus for divisibility (Notion runs 480 logical shards over far fewer physical hosts), decoupling placement from hardware.
- autovacuum runs per-partition (smaller, parallel) instead of over one 10M-row heap.

## 7.1 Lifecycle State Machine

```text
captured
  -> extracted
  -> candidate
  -> active
  -> superseded
  -> invalidated
  -> deleted

quarantined
  -> candidate
  -> expired

procedure_candidate
  -> validated
  -> active
  -> retired
```

State rules:

- `captured` means raw episode/resource exists.
- `candidate` means a derived memory exists but cannot be injected as trusted context yet.
- `active` means policy allows retrieval.
- `superseded` remains citable for history but loses default retrieval priority.
- `invalidated` cannot appear in recall except audit/debug.
- `deleted` means all recall-affecting derived material is removed or tombstoned according to policy.
- `quarantined` can be inspected but is not default recall context.

## 7.2 Evidence Ledger

Every active semantic, procedural, belief, or resource-derived memory has at least one evidence path:

```text
memory_unit
  -> citation
  -> episode/resource/span/hash
  -> actor/source/trust event
```

Allowed exceptions:

- manually entered high-trust admin memory, with actor and reason
- imported system seed memory, with dataset/version hash
- synthetic eval fixture memory, with fixture ID

No exception bypasses tenant/scope/privacy policy.

A citation is a **point-in-belief-time** artifact: its `validity` snapshot (`08` §4.1) is the window as believed when it was emitted, reproducible with the archived trace's `transaction_as_of` and `valid_at` (§7.3a) and **never silently rewritten** by a later retroactive correction. A citation whose target episode was hard-deleted/crypto-shredded (`06` §6.2) simply stops resolving at recall (the `deletion_generation` filter), never resurfaces stale bytes.

## 7.3 Bitemporal Facts

Semantic facts carry two clocks:

| Field | Meaning |
|---|---|
| `valid_from` | when the fact became true in the represented world |
| `valid_to` | when it stopped being true in the represented world |
| `observed_at` | when MemPhant observed evidence |
| `transaction_from` | when MemPhant wrote the fact |
| `transaction_to` | when MemPhant superseded/invalidated it; **NULL = the open/current generation** |

Contradictions create edges. They do not overwrite rows in place.

### 7.3a Append-Only Generations (the write discipline)

**Transaction-time is append-only; valid-time is the only axis rewritten into the past, and only by appending a new row.** Each fact is a chain of immutable belief-generations on one `subject_key`. A generation is *open* while `transaction_to IS NULL` (what MemPhant believes now) and *closed* once `transaction_to` is set (what it used to believe). `correct`, supersession, and `invalidate` are the **same physical operation**: close the prior open generation (`transaction_to = now`, `state → superseded|invalidated`) and `INSERT` a new generation with the corrected `valid_from`/`valid_to`/`state`/`body` — **never** an `UPDATE` of a closed row's `valid_*`/`transaction_from`/`body`. This is the Fowler/SQL:2011-system-versioning/XTDB rule applied to memory: the represented world can be rewritten retroactively, but *how belief evolved* is permanent — which is exactly what makes independent `transaction_as_of` + `valid_at` audit-replay reproducible (`08` §3.1).

| field write | (a) supersede (new truth now) | (b) retroactive correction (R18) | (c) invalidate |
|---|---|---|---|
| close `G_old`: `transaction_to`, `state` | `now`, `superseded` | `now`, `superseded` | `now`, `invalidated` |
| `G_old` `valid_*`/`transaction_from`/`body` | **never touched** | **never touched** | **never touched** |
| insert `G_new`: `valid_from` | `= T_change` (now) | `= corrected past boundary` | *(no `G_new`)* |
| insert `G_new`: `transaction_to` | `NULL` (open) | `NULL` (open) | — |

`state='active' ⟺ transaction_to IS NULL` (a DB-lint invariant for semantic/belief). At most one open generation per `(tenant_id, subject_key)` — the close+insert is **one transaction** (`03` §4), and a partial-unique index makes a second open structurally impossible (§7). The supersedes-chain `correct` response (`08` §4.2) is just the application-time *view* of this append-only row set — the public contract is unchanged.

## 7.4 Candidate Whitelist and Citations

Recall returns memory candidates with IDs. Any subsequent model answer that cites memory must cite from that candidate whitelist or perform an explicit mid-run recall that writes a new trace. This mirrors the Syndai-proven chokepoint pattern: memory access is centralized and auditable, not scattered through prompts.

## 8. Decay and FSRS-Inspired DSR

FSRS is the modern variant of Wozniak's **DSR** (Difficulty / Stability / Retrievability) model. Two facts about the current algorithm shape the schema:

1. **Per-unit state is only `(stability, difficulty)`.** Retrievability is *computed on demand* from stability and elapsed time — `R(t,S) = (1 + FACTOR · t/(9·S))^DECAY` — not stored. So `memory_unit` persists `stability_days` and `difficulty`; a stored `retrievability` column would be a denormalized cache, not source of truth.
2. **The parameters are global, not per-unit.** Current FSRS is **FSRS-6, a 21-weight vector**. "Learned fitting" means training those 21 weights from a corpus of retrieval-review traces (a global optimizer), *not* fitting a curve per memory unit. FSRS-5 used 19 weights, FSRS-4 used 17 — so the spec pins the *interface* (the DSR fields + a review-event log) and treats the weight count as versioned, never hardcoded.

Implementation: the Rust decay kernel should wrap the **`fsrs-rs`** crate (`open-spaced-repetition/fsrs-rs`: `MemoryState`, `next_states()`, `compute_parameters()`) rather than reimplementing the math — it is the highest-scoring FSRS implementation and already does SM-2 migration. Listed in `03` §3 dependency defaults.

> **Honesty caveat (invariant #15 — "benchmarks decide").** Applying FSRS/DSR to *agent* memory decay is **unvalidated** — no public benchmark shows it improving agent recall; it is an Anki-derived prior, and the 2026 survey "Memory for Autonomous LLM Agents" (arXiv:2603.07670 §9.8) treats spaced-repetition for agent memory as an *aspirational future direction*, noting shipped agent systems use the simpler **Ebbinghaus exponential** decay (MemoryBank, arXiv:2305.10250) or cruder heuristics — not a trained DSR. FSRS itself needs ~1,000+ human review grades to fit its weights, and agent memory has no analogous review signal. The data-gating posture below is the correct hedge: ship the fields and fixed priors now, turn on learned fitting only when reinforcement traces exist, and treat "decay helps" as an **ablation hypothesis** (`05` §9), not a settled lever. **The ablation target is `MemoryStress`** (a 1,000-session / 10-month longitudinal benchmark with 40 contradiction chains, Apache-2.0; `12`) — short benchmarks (LoCoMo/LME) are too brief to exercise decay; FSRS must beat plain exponential decay *on the degradation curve* or be replaced by it.

**Build timing (R82).** The first public build ships the DSR *fields* (frozen in §7) and the append-only `review_event` *ledger capture* (§8.2 — rows are written from day one because reinforcement/outcome labels cannot be backfilled; the `mark` verb, `08`, is a first-class producer). The v1 *ranking* signal is plain recency/exponential decay — the baseline shipped agent systems actually use. The FSRS fixed-prior **fold engine** (§8.2) is built at rung 11, and learned fitting (the 21-weight optimizer) activates only after MemPhant has enough retrieval-review traces to estimate its own parameters without cargo-culting Anki review data. The rung-11 gate runs an **internally-executed MemoryStress-style longitudinal suite in MemPhant's own harness** — running the corpus ourselves is an internal measurement; anchoring to the vendor's published leaderboard number stays forbidden (`12` §8). Implementation note: the `fsrs` crate's v6 line natively supports **per-card desired retention** and a cost-conditioned retention policy — reach for the native API rather than hand-rolling per-unit retention above the crate.

**Worked update (fixed-prior).** A `project_facts` unit starts `stability_days = 7`, `difficulty = 5`. It is recalled-and-confirmed (a positive reinforcement) on days 3, 12, 30 — three *distinct* observations (§2.3 dedup ensures retries do not count). Each confirmation raises stability (less forgetting) per the FSRS stability-after-success update; by day 30 `stability_days ≈ 60`, so computed retrievability at day 45 is high and the fact stays in default recall. A contradicting higher-trust fact instead routes through §3.1 (supersede), not through decay — decay lowers *priority*, correction changes *truth*.

> **Scope of decay, stated exactly (verified 2026-07-30).** Decay is a **recall-time score multiplier and nothing else**: `candidate.fused_score *= candidate.decay.retrievability` (`crates/memphant-core/src/lib.rs:7364`), plus a composite term at `lib.rs:9290`. It reorders candidates; **it changes no state, moves no tier, and demotes nothing.** The report-level phrase "episodic = decay-to-cold" therefore describes §13.3's tiering job, not this section — decay supplies one *input predicate* to that job and never performs the transition itself.

### 8.2 Reinforcement is Replayed from a Review Ledger, Never Incrementally Mutated (at-least-once correctness)

`fsrs-rs` `next_states()` takes the **current** `MemoryState` *(stability, difficulty)* plus `days_elapsed` and returns the *next* `MemoryState` — by its signature `next_states(current_memory_state: Option<MemoryState>, desired_retention, days_elapsed)` it is a **read-modify-write on a continuous value**, not a pure function of an immutable event log. So an in-place `update_decay` that reads `stability_days`, calls `next_states`, and writes the new `stability_days` **double-applies a redelivered reinforcement** (pgmq vt expiry / Temporal activity retry, both at-least-once): the same recall-confirmation bumps stability twice, the fact looks more reinforced than the evidence warrants, and at 10M scale this is a systematic over-retention drift. This is the **same RMW hazard as confidence (§5.1a)** and gets the **same event-sourced fix** — and it is *why* `reinforcement_count` is already specified as "distinct observations, not raw re-arrivals" (§7 column comment): that invariant was unenforceable without the ledger below.

- **A `review_event` ledger is the source of truth**, keyed `UNIQUE (tenant_id, memory_unit_id, source_event_id)` (tenant_id leads — `PARTITION BY HASH (tenant_id)`, §7.0; the §9 stage already logs "review/reinforcement events"; this makes it the *authoritative* input, not telemetry). A redelivered reinforcement is an `ON CONFLICT DO NOTHING` insert — the redundant event never reaches the kernel.
- **`(stability_days, difficulty)` is replayed, not accumulated.** `update_decay` folds the unit's *distinct* `review_event`s through `fsrs-rs` `next_states()` in `observed_at` order from the unit's birth state. Replaying the same deduped ledger yields the same `MemoryState` every time — idempotent under redelivery, reordering, and crash-resume, and it is the **only** way to honor the `Clock`-seam determinism the eval harness already requires (`03` §6: "a replay at fixed `t` reproduces"). The kernel keeps its `Clock` seam; the ledger feeds it deterministic, deduped, ordered events.
- **Bounded by a checkpoint, never O(all history).** A fact recalled thousands of times would make a from-birth replay grow without bound, so `update_decay` checkpoints: persist `(stability_days, difficulty, last_event_id)` and on the next round fold only `review_event`s after `last_event_id`. The checkpoint is a *cache of a pure fold*, re-derivable from the ledger — so it is safe to drop and recompute, unlike a mutable accumulator that loses the truth if it drifts. Folding only new events past a checkpoint is O(new reviews), matching the §9.3 "cost linear in new memories, not total corpus" invariant.

The DSR fields (`stability_days`, `difficulty`) stay exactly as frozen in §7; what changes is **how they are written** — replayed from a deduped ledger, never `stability_days = f(stability_days)`. Decay stays off the hot path (`02` §5.1); the ledger fold runs in the background `update_decay` job under the same at-least-once-safe discipline as `reflect`.

**Capture now, fold at rung 11 (R77/R82).** The ledger *capture* (the `ON CONFLICT DO NOTHING` inserts, including grades from the `mark` outcome verb, `08`) ships in v1 — it is the non-backfillable half. The *fold* (`update_decay` running `fsrs-rs` over the ledger) is rung-11 work; until that rung fires, ranking uses plain recency/exponential over `last_reinforced_at` and the ledger simply accumulates the evidence the rung-11 ablation will need.

### 8.1 Active Freshness (decay is not enough for staleness)

Decay and bitemporal validity (§3) are both **reactive** — validity only closes when a *contradiction arrives*, and decay only lowers priority over time. Neither catches the dominant long-horizon failure: a fact that is *confidently wrong* because the world changed and nobody told the system ("employer = Acme" is right until the user changes jobs). The field names this `memory staleness` as an open problem. So high-churn fact *types* get **active freshness**, not just passive decay:

- A semantic unit's `subject_key`/predicate carries a **churn class** (`stable` — name, birthday; `slow` — preferences; `volatile` — employer, role, location, current-project). Volatile-class facts get a much shorter freshness horizon.
- `memory_unit.last_confirmed_at` records the last confirmation/disconfirmation event that kept the open generation credible, and `freshness_due_at` is the indexed due-scan key. No separate freshness queue exists; the background job scans due rows and writes ordinary observations, trust events, or supersession generations.
- Past its horizon a volatile fact is **down-weighted and flagged `unconfirmed-stale`** in recall (surfaced with "last confirmed on X"), and `reflect` may **proactively re-confirm** it (a cheap targeted question or a re-observation check) rather than waiting for a contradiction. A re-confirmation advances `last_confirmed_at`/`freshness_due_at`; a disconfirmation supersedes (§3.1).
- This is the staleness counterpart to decay: **decay forgets the unimportant; active freshness re-checks the perishable.** Churn class is a `payload` field, gated behind an ablation (does active freshness improve temporal-reasoning accuracy on 6-month-old facts) before it drives default recall.

Policy examples:

| Memory | Desired retention | Decay |
|---|---|---|
| identity/preferences | high | slow, needs explicit invalidation |
| project facts | medium-high | validity-window aware |
| web/world-state | low-medium | aggressive recency decay |
| untrusted observations | low | expire unless corroborated |
| validated procedures | high | stable after successful replay |
| failed procedures | medium | retained as warning until obsolete |

**Default retention priors (R91 — config, not a concept).** The table above is operationalized as a checked-in mapping from protected-category/churn-class to a `desired_retention` prior, so consequence-weighted retention is a default, never a per-unit hand decision: `identity`/protected categories → 0.95 (slow decay, explicit invalidation only); `stable` churn → 0.9; `slow` churn (preferences) → 0.9; `volatile` churn (employer/role/current-project) → 0.8 + a short freshness horizon (§8.1); `web/world-state` → 0.7; uncorroborated beliefs → expiry-by-default (§5.2). Priors are tunable config behind `compiler_version`; the answer to "favorite color vs the API-secret rotation schedule" is that the latter is a **protected category** (credentials — never stored as recallable memory, secret refs only, `06` §2.1) — consequence is handled by category + `arg_risk` + retention prior, never by a stored importance scalar (`26` §4).

## 9. Consolidation Cycle (`reflect`)

```text
episodes -> candidate facts/entities/resources
episodes -> failure/success strategies
beliefs -> semantic facts when corroborated (independent sources, §5)
procedures -> promoted only after validation
low-trust records -> quarantine/expire unless reinforced
```

This is the "sleep-time compute" loop (Letta, 2026 — §1.1). It is background work, never required for raw capture. `reflect` is a **public verb** (`08` §recall/reflect), so it gets a contract parallel to recall — it is the moment poisoning either survives or is stopped, and it cannot be a black box:

- **Stage contract** — `reflect` runs: (1) extraction of candidate units from new episodes; (2) contradiction detection over affected `subject_key`s (§3.1); (3) corroboration evaluation with the **independent-source** gate (§5); (4) promotion of cleared candidates; (5) decay/reinforcement updates (§8); (6) trust-event writes. Each stage is idempotent and resumable.
- **Trace contract** — every `reflect` run emits a durable trace: episodes consumed, candidates created, contradictions found/resolved, promotions (with the corroborating source set and their independence check), demotions/expiries, and cost/latency. The recall trace's `consolidation_lag` field (`05` §3) is derived from how far `reflect` is behind capture.
- **Cost ownership** — `reflect` owns the expensive LLM-extraction calls (the real cost center, `02` §3); it is demand-tiered and batched, never on the capture or recall hot path.
- **Security** — promotion-on-corroboration is the documented poisoning amplifier; the `corroboration-farming / Sybil poisoning` suite (`05` §10, `06` §7) asserts that K mutually-reinforcing low-trust observations from one origin never clear promotion.

Two rung-gated `reflect` extensions carry frozen interface bits now, behavior at their rung (R80): a **retrievability probe** (`retrievability_probe_enabled`) — after promotion, run 2–3 deterministic paraphrase probes through recall in a marked synthetic mode (no trace pollution, no reinforcement) and record a `findability` payload field on failure, catching subject-key-canonicalizer drift (§3.3) within one reflect cycle instead of at the next benchmark run; and **demand-paged re-extraction** (`miss_repair_extraction_enabled`, job `reextract_on_miss`, `02` §5.1) — when a trace shows the top activation-row failure ("answer-bearing unit was never written," `27` §4), re-run extraction on that episode *conditioned on the missed query*, keyed `(episode_id, query_features_hash, compiler_version)`. Re-extraction is invariant #1's retrieval-side payoff: only a system that kept the recoverable raw episode can mint the unit the workload actually needed; the §9.2 admission gate absorbs the near-dup pressure of query-conditioned units. A query can direct extraction *attention*, never raise trust.

### 9.1 One Episode, Multiple Kinds (the fan-out)

A single episode rarely maps to one unit. `reflect` extraction fans one episode into units of several kinds, all citing it (`derived_from`/`cites`); the same fact threads across kinds by *trust maturity*, not duplication. Worked example — episode: *a CI log shows "staging pins Node 24.15.0"* (`source_kind=tool`, `verified_tool`):

| Kind | Unit | First-pass state |
|---|---|---|
| episodic | the raw CI-log episode | `captured` (ground truth) |
| belief | "staging pins Node 24.15.0" | `active`, confidence ~0.6 (single `verified_tool` origin) |
| semantic | — | blocked: one origin fails the independence gate (§5) |
| procedural | (if the log shows a fix) "pin Node to avoid Playwright hang" | `procedure_candidate`, awaits replay (§4.2) |
| resource | the CI-log artifact + chunks | `embedded` (§6.1) |

A **second independent** observation (e.g. a `trusted_user` PR confirming it) promotes the belief precursor → a semantic unit (`derived_from` the belief, `cites` both episodes). One fact, three rows, three kinds, one evidence chain — which is why the kinds are policy-rows, not storage silos. A `forget` on the source episode (§10) invalidates every kind it spawned in one deletion generation, and the single expensive LLM extraction pass producing the whole multi-kind candidate set is why `reflect` owns extraction cost (§9).

### 9.2 The Write-Time Quality Gate (admission control, not a later sweep)

The dominant production failure is **over-storage**: a 32-day Mem0 audit found **97.8% of 10,134 entries were junk** (dups, re-extraction, contradictory accumulation), and the Harvard/D³ study (arXiv:2505.16067) found indiscriminate "add-all" storage performs *worse than no memory at all*. The fix is admission control **at ingest**, not a cleanup pass afterward — so `reflect`'s dedup (§2.3), corroboration (§5), and contradiction detection (§3.1) are a **quality gate the candidate passes before it becomes a recallable unit**, not a sweep that reconciles junk later. A candidate that is a near-dup of an existing unit, or contradicts one, or is a single-origin low-trust claim, is collapsed/edged/held *at write*, never stored as a fresh competing row (the Mem0 #4896 "two contradictory facts" / #4536 "delete-on-conflict" / #1674 "re-add deletes old" failures are all the absence of this gate).

### 9.3 Consolidation Invariants at Scale (so reflect never falls behind)

`reflect` must stay viable at 10K+ episodes. These are **invariants, not emergent behavior** (the production-proven shape, Hindsight/Vectorize):

- **Cost is linear in *new* memories, not total corpus.** A retain that processes 10 chunks costs the same whether the store holds 100 or 1,000,000 units — extraction never scans what is already stored.
- **Bounded batches + bisection retry.** A `reflect` round caps memories-per-round and LLM-batch-size; a batch that fails (one malformed/oversized item) **bisects 8→4→2→1** so one bad memory never blocks the rest (`02` §3.1 / §5).
- **Hierarchical retrieval, no full-corpus scan.** Consolidation reads top-down (mental-model/summary → observations → raw facts), so reflect quality is decoupled from total memory count.
- **Async, never on the user path.** `reflect` runs in the background worker; recall never blocks on it, and falls back + declares `consolidation_lag` when behind (`02` §3.1).

### 9.4 Stage Checkpoint + Resume (making "idempotent and resumable" real)

§9's "each stage is idempotent and resumable" was **asserted with no mechanism**. Under at-least-once redelivery a `reflect` that crashes after stage 4 (promotion) and before stage 6 (trust-event writes) must **resume from stage 5, not re-run stages 1–4** — re-running extraction re-pays the expensive LLM cost (§9 cost center) and, without the §5.1a/§8.2/§3.4 idempotency, would double-apply accumulators. The checkpoint:

- **A `stage_completed` marker per `(job, stage)`.** `reflect`'s six stages (extract → detect → corroborate → promote → decay → trust) write a stage-keyed marker — either a `reflect_stage_progress{job_id, stage, completed_at}` row or a `stage_completed_mask` bitfield on `job_state` (one bit per stage). On redelivery, a completed stage is **skipped**, so resume re-enters at the first incomplete stage.
- **Each stage is independently idempotent**, so a stage that the marker says is incomplete (crashed mid-stage) is *safe to fully re-run*: extraction re-derives the same UUIDv7 candidate units from the `(job_type, target_id, compiler_version)` key (`02` §3.0); detect/corroborate/promote are deterministic functions of current state; decay/trust replay from their ledgers (§8.2). The marker is a **cost optimization** (skip finished expensive stages), and per-stage idempotency is the **correctness floor** (a crash mid-stage is harmless even if the marker is wrong). Two layers, so a lost marker degrades to "re-run a stage," never to "corrupt state."
- **Markers are keyed by `compiler_version`** — a `reflect` logic change bumps the version, invalidates stale markers, and forces a clean re-run rather than resuming half-way through an old pipeline into new code.

This is additive to the §3.1 idempotency keys and the §5.1a/§8.2 ledgers — it does not introduce a new state store; the marker lives on the existing `job_state` row (`14` §3.1).

## 10. Correction and Forgetting

Corrections are first-class events:

```text
correction request
  -> identify target memory/episode/resource
  -> write correction event
  -> supersede or invalidate affected memory units
  -> re-run citations/edges/embeddings as needed
  -> archive trace of changed recall behavior
```

Forgetting is stronger:

```text
forget request
  -> authorize tenant/scope/actor
  -> assign deletion_generation
  -> hide affected memory from recall immediately
  -> invalidate derived units, embeddings, edges, caches, traces
  -> delete or tombstone raw blobs according to policy/legal basis
  -> verify no recall path returns forgotten content
```

Deletion completeness is an eval, not an ops footnote.

## 11. Agent-Tree Scoping

MemPhant uses neutral terms:

| Neutral concept | Meaning |
|---|---|
| `scope` | tenant-defined workspace/project/thread/context boundary |
| `agent_node` | an agent's node in a parent/child **access** tree (agents only) — distinct from the provenance `actor` entity (`03` §5.1): an `agent_node` is one *kind* of actor (`source_kind=agent`), but most actors (user/tool/web/system) have no `agent_node`. The independence gate (§5) counts **`actor`s**; the inheritance gate (§11.1) gates on **`agent_node.level`** |
| `level` | inheritance/access tier in that tree |
| `delegation_depth` | runtime recursion/admission property, separate from access level |

Syndai's `L0`/`L1+` behavior maps into this adapter. Checked Syndai code blocks L1+ from user facts, persona, episodic, and behavioral memory. MemPhant must preserve that guarantee through policy, not copy the names.

**Two trees, one invariant binding them:** `scope` (workspace/project/thread boundary) and `agent_node` (actor parent/child) are separate trees; an `agent_node` carries `scope_id` (where it acts), `parent_agent_node_id` (tree position), and `level` (access tier). The cross-tree invariant: **a child `agent_node`'s `scope_id` must be the parent's scope or a descendant of it, and its `level ≥` the parent's** — a child can never act in a wider scope or at a lower (more-privileged) level than its parent. This makes `level` a constrained tier, not a free-floating int.

### 11.0 Scope Tree + Inheritance Policy (the access-path physical model)

`scope` is **adjacency-list (`parent_scope_id` = source of truth) + a cached `materialized_path ltree`** read accelerator — the hot "resolve scope S + its admitted ancestors" query is one indexed `@>` lookup, **no per-recall recursion**. The dead `path_hash` is replaced; depth is bounded (`scope_depth ≤ MEMPHANT_MAX_SCOPE_DEPTH = 32`, the defense against an adversarial deep-tree latency cliff). `scope`, `scope_policy`, and `agent_node` are the **tree, not the memory** — small per-tenant metadata — so they are **UNPARTITIONED** (a deliberate §7.0 carve-out; `tenant_id` still leads every PK/index). A scope subtree lives within one tenant, so the ancestor walk never fans across tenant partitions and composes cleanly with the §7.0 hash pruning (`02` §2.1b). Re-parent is a bounded subtree path-rewrite (`materialized_path <@ old`); `scope_policy` keys on stable `scope_id`, so re-parent never touches policy rows.

```sql
scope (
  id uuid NOT NULL, tenant_id uuid NOT NULL, parent_scope_id uuid,  -- within-tenant FK (no cross-tenant parent)
  kind text NOT NULL, external_ref text,
  materialized_path ltree NOT NULL,            -- root→self; labels = UUID hex; GiST @> serves the ancestor walk
  scope_depth smallint NOT NULL CHECK (scope_depth <= 32),
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id)
)  -- NOT partitioned; GiST (tenant_id, materialized_path gist_ltree_ops(siglen=100)) + btree (tenant_id, parent_scope_id)
   -- siglen EXPLICIT: the gist_ltree_ops default signature is only 8 bytes — too imprecise for
   -- deep trees with UUID-hex labels; set (siglen=100) and benchmark (R74).

-- The inheritance-policy object: deny-by-default; a grant is an EXPLICIT row, never a memory_edge.
scope_policy (
  id uuid NOT NULL, tenant_id uuid NOT NULL,
  scope_id uuid NOT NULL,                       -- the scope whose memory is shared
  kind text NOT NULL CHECK (kind IN ('episodic','semantic','procedural','belief','resource','preference')),
  direction text NOT NULL CHECK (direction IN ('inherit','grant')),  -- inherit=downward; grant=explicit cross-scope
  min_level smallint NOT NULL CHECK (min_level BETWEEN 0 AND 64),     -- admit actors at this agent_node level OR lower
  grantee_scope_id uuid,                        -- NULL for inherit; the target scope for grant
  admit boolean NOT NULL DEFAULT true,          -- false = explicit deny override
  PRIMARY KEY (tenant_id, id),
  CHECK ((direction='grant') = (grantee_scope_id IS NOT NULL)),     -- implicit sibling access is STRUCTURALLY unrepresentable
  CHECK (grantee_scope_id IS DISTINCT FROM scope_id)
)  -- NOT partitioned; (tenant_id, scope_id, kind) WHERE direction='inherit'; (tenant_id, grantee_scope_id, kind) WHERE direction='grant'
```

`resolve_policy(tenant, S, actor, level)` (the `03` §5.2 Stage-0 chokepoint): resolve ancestors via `materialized_path @> S_path` (no recursion), then `policy.scopes = {S} ∪ {ancestors with an `inherit` row admitting (kind, ≥ level)} ∪ {explicit grants into S}`. A child can never *widen* (admitted ⊆ ancestors(S)). Protected categories (`06` §2.1) are kinds with **no `inherit` row** (or an `admit=false` override), so L1+ "inherits nothing there" falls out of the schema, not special-case code. The `memphant-scope-inheritance` proptest now *represents* a grant (generates `grant` rows): sibling access is **off iff no grant row exists** — falsifiable. Requires the `ltree` extension (core contrib, like `pg_trgm`).

### 11.1 Inheritance and Recall Composition (the exact rules)

The frozen contract (`00` §2): explicit inheritance, **no implicit sibling access**. Made operational:

- **Downward-restrictive, not upward-open.** A child inherits a parent's memory only for kinds with an explicit `scope_policy` `inherit` row admitting `(kind, ≥ level)` (§11.0), and **never** the protected categories (`06` §2.1: identity, user facts, persona, episodic, behavioral) — which simply carry no `inherit` row. L1+ is exactly "a child that inherits nothing in those categories."
- **Siblings are isolated, always.** Two children of one parent share no memory by default; cross-sibling recall needs an explicit `scope_policy` **grant row** (`direction='grant'`, names `grantee_scope_id`) — **never** a `memory_edge`, never proximity (the grant is representable + falsifiable, pinned by the `memphant-scope-inheritance` lane, `03` §6.1).
- **Recall composition = walk-up-then-filter.** A recall in scope S draws candidates from `S ∪ {admitted ancestors} ∪ {explicit grants}` via `resolve_policy` (§11.0), then applies the Stage-0 gate. A child can never *widen* access by querying (admitted ⊆ ancestors(S)).
- **A child cannot write into a parent scope.** Consolidation a child produces lands in the child's scope; promotion to a parent is an explicit policy-gated merge, not a default of `reflect` — this stops a low-trust child from poisoning a shared parent.
- **Evaluated at recall, not copied at write.** No memory is duplicated into child scopes, so a later parent-side policy change or `forget` is immediately reflected in every child's recall (copy-at-write would make `forget` incomplete — a `06` §6 violation).

### 11.2 Intra-Scope Concurrency (multiple agents, one shared scope)

The scope tree solves *isolation*; it does not solve two agents writing the *same* shared scope at once — and inter-agent memory misalignment is a measured failure (MAST, arXiv:2503.13657: **36.9%** of multi-agent failures). Concurrency within a shared scope is handled by **per-memory-kind isolation levels** (the field's "different memory regions need different isolation simultaneously" — UCSD arXiv:2603.10062), not one global lock:

| Kind / region | Isolation | Why |
|---|---|---|
| once-only claims (a procedure marked `validated`, a scope-merge) | **serializable** | exactly one writer may win; a double-claim is a correctness bug |
| append-only shared findings (episodes, belief candidates) | **read-committed** | many agents append concurrently; no coordination needed beyond commit visibility |
| private per-agent scratch (an agent's own un-promoted beliefs) | **none** | not shared; no contention |

- **Same-episode contradictions resolve by recency/temporal supersession**, not last-writer-wins-blindly: two agents extracting contradictory facts from one episode produce a `contradicts` edge resolved by the §3.1 rule (higher trust, else newer `valid_from` supersedes). Per-fact provenance is already carried (the corroboration source set, §5), so "who wrote it" is auditable. (Adding *source-rank* to the tiebreak is a MemPhant design option, not established prior art — the validated baseline is recency-based supersession.)
- **Visibility is bounded, not instant.** A write is visible to a concurrent reader within ~one search round-trip (sub-second in the proven designs); a separate async settle window is where contradiction detection runs. Recall never assumes a write made microseconds ago is already consolidated — that is what `consolidation_lag` (`02` §3.1) surfaces.

## 12. Pinned Scope Block (the working-set affordance — R88)

The one working-memory affordance the substrate owns: **ONE content-editable pinned block per scope**. It is a *packing-stage artifact* — behavior is owned by `05` §1.2 (packing) and `08` §3–4 (surface); this section owns the storage row and the trust rules. Production grounding: Syndai's persona — a persisted, ~300-token-bounded, user/agent-editable block compiled into every prompt with history and rollback — is the field's Letta-block job done right; N pinned unit-refs was rejected because order-only pins can be silently dropped (a broken promise) and N presence-guaranteed refs recreate the measured over-personalization harm (`05` §1.5).

```sql
scope_block (
  id uuid NOT NULL, tenant_id uuid NOT NULL, scope_id uuid NOT NULL,
  content text NOT NULL,                    -- the block body; rendered as DATA in the pack
  token_limit int NOT NULL DEFAULT 300,     -- hard Stage-7 sub-budget
  version int NOT NULL,                     -- append-only: an edit INSERTs version+1 (prior versions retained)
  updated_by_actor_id uuid NOT NULL,        -- every edit writes a trust_event (audited)
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, scope_id, version)
)  -- UNPARTITIONED tree-side scope state, like scope/scope_policy (§7.0 carve-out); current = max(version)
```

Rules:

- **Guaranteed presence, explicit truncation.** The current block is packed first under its hard token sub-budget and is **never silently dropped** — over-budget content is truncated with an explicit `pinned_block_truncated` label; the relevance gate (`05` §1.5) may label it, never remove it. `inclusion_reason: pinned_block`.
- **Trust-capped: the block is data.** It renders delimited as data (invariant #4), is never `high_risk_arg`-eligible, and **never counts as corroboration** (`§5` independence gate ignores it). Editing requires a `trusted_user`/`trusted_system`/admin actor.
- **Append-only + audited.** Edits insert a new version and write a `trust_event`; history is inspectable (`19`). No in-place mutation (the §7.3a discipline, simplified: version-increment instead of bitemporal clocks — the block has no represented-world validity to model).
- **Forget clears it.** Scope-`forget` deletes all versions under the same deletion generation; the file adapter projects the current block at `/memories/pinned.md` (`08` §5.1a), so file-agents edit it through the same gates.
- **Restraint-gated.** Block content is in-scope for the over-personalization launch gate (`27` §1) — a pinned block is exactly where the §1.5 thin-pack harm concentrates, so the gate measures it rather than trusting it. The gate's *instrument* is **MemSyco-Bench**, substituted for the unrunnable OP-Bench / unlicensed PS-Bench (`27` §1, `26` §3 D-2026-07-30c); the threshold and the in-scope rule here are unchanged by that substitution.

## 13. Governance Core (typed write-router, memory planes, principal scoping)

> **READ THIS FIRST — §13 IS SPECIFICATION, NOT DESCRIPTION.** Everything in
> §13 is **SPECCED-UNBUILT** unless a row says otherwise. It was written
> 2026-07-30 (W3.1) because the governance core previously existed only in a
> campaign report (`docs/reports/2026-07-11-prosumer-memory-campaign-report.md`
> §"five-plane architecture"), never in this suite — so "finish all substrates"
> had no contract to finish against. Two false BUILT claims in `STATUS.md` in
> the week before it was written came from exactly that confusion. **Nothing in
> §13 may be cited as evidence that code exists.** The verified code state of
> every claim is recorded inline with a file path, and consolidated in §13.6.

### 13.0 Verified starting state (2026-07-30)

Every row was re-verified against the working tree at the time of writing.

| Intended mechanism | Verified code reality |
|---|---|
| typed write-router | **absent.** Admission is one linear loop with an if/else ladder on `candidate.admission_hint` (`crates/memphant-core/src/lib.rs:11500`, `:11575`, `:11699`); `kind` enters only as data |
| knowledge = supersession | **BUILT 2026-07-31** (`ffa640b8`). The hardcoded literal is gone: the target filter now comes from `supersedes_own_kind`, which dispatches through the exhaustive `write_router_arm`. Because an arm may only name its own kind there, RW-3 is structural and `episodic_arm` provably supersedes nothing — a cross-kind bug the constant permitted (an Episodic candidate closing a Semantic generation) is fixed and regression-tested |
| episodic = decay-to-cold | **absent.** Decay is a recall-time score multiplier only: `candidate.fused_score *= candidate.decay.retrievability` (`lib.rs:7364`) plus a composite term (`lib.rs:9290`). It reorders; **nothing changes state** |
| procedural = evidence-gated | **read-gate only.** `UnitState::Validated` is required at `lib.rs:8362` (and in the recall-eligibility predicates `lib.rs:3284-3285`, `store.rs:3724`, `:3765`); **no write path in `crates/*/src/` ever transitions a unit into `Validated`** — every non-test occurrence is a read or a serialization arm |
| preference = chain-head → hot | **absent and inexpressible.** No `Preference` variant (`crates/memphant-types/src/lib.rs:968`); no `chain_head` anywhere in the tree |
| hot plane | **absent.** `episode.retention_tier` (`hot\|warm\|cold`, `memphant_migrations/versions/20260703_001_wsa_bootstrap.sql:278`, partial index `:826`) has **0 readers and 0 writers** in `crates/` (`grep -rn retention_tier crates --include='*.rs'` → 0 hits). Every episode is `'hot'` forever. The `tier_episode` job named in §2.4, `14` §3, and `02` §6 **does not exist in code** |
| cold plane / demotion | **absent.** `demote`/`demotion`/`cold` appear in **no** Rust file under `crates/`. What exists is the inverse — hard `forget` with tombstones (`crates/memphant-store-postgres/src/store.rs:1325`, `:1628`) |
| `MemoryKind` | **`Episodic \| Semantic \| Procedural \| Belief \| Resource \| Preference`** (`crates/memphant-types/src/lib.rs:968`, `ALL: [Self; 6]`) since 2026-07-31. No `Knowledge` (§13.2c) and no working-state kind. **Row kept, not deleted:** the five-kind reading above is what §13.0 verified on 2026-07-30, and the delta is the point |

### 13.1 The typed write-router

The router is the admission stage of `reflect` (§9 stage 4), restructured. Today
that stage is a single loop that branches on `admission_hint`; the contract
below inverts the dispatch: **kind first, hint second.**

```text
router(candidate) =
  match candidate.kind {                  // exhaustive, no wildcard arm
    Episodic     => episodic_arm(...),    // capture + tier scheduling
    Semantic     => knowledge_arm(...),   // supersession (§3, §7.3a)
    Procedural   => procedural_arm(...),  // evidence gate (§4.2)
    Belief       => belief_arm(...),      // corroboration gate (§5)
    Resource     => resource_arm(...),    // extractor lifecycle (§6.1)
    Preference   => preference_arm(...),  // chain-head -> hot   (§13.2a, kind unminted)
    WorkingState => working_state_arm(..) // retain + compact    (§13.2b, kind unminted)
  } -> WriteDecision { action, unit_deltas, edge_deltas, reason }
```

**Router invariants.** These are the testable part; an implementation that
satisfies the arms but violates these is not the router.

- **RW-1 Kind-totality.** The dispatch is an exhaustive `match` on `MemoryKind`
  with **no `_` catch-all**. This is the enforcement mechanism for §13.2: minting
  a new kind must fail to compile until its arm exists. A wildcard arm silently
  reintroduces the "`kind` enters only as data" defect and is a contract
  violation, not a style preference.
- **RW-2 Arm purity.** An arm is a deterministic function of
  `(candidate, in-scope existing units, now)`. **No LLM call, no network, no
  clock read other than the passed `now`.** Extraction cost stays in `reflect`
  stage 1 (§9); the router is the cheap decision layer. This is what preserves
  deterministic writes and makes the golden write-compiler tests
  (`crates/memphant-core/tests/write_compiler_golden.rs`) meaningful.
- **RW-3 Own-kind writes only.** An arm mints, closes, or edges units of **its
  own kind**. The single sanctioned cross-kind transition is belief → semantic
  promotion, which is owned by `belief_arm` and gated by §5 independence; it is
  named here so it stays the only one.
- **RW-4 Append-only transaction time.** Every arm obeys §7.3a: close the open
  generation and INSERT: never an in-place `UPDATE` of `valid_*`,
  `transaction_from`, or `body`.
- **RW-5 Idempotence.** Router output is keyed by
  `(job_type, target_id, compiler_version)` (`14` §3); replaying a job produces
  the same `WriteDecision` and no duplicate rows.
- **RW-6 Traced decisions.** Every invocation appends `{arm, action, reason}` to
  the `reflect` trace (§9 trace contract). A write outcome that cannot be
  attributed to an arm and a reason is a defect — the write-side counterpart of
  `27` §0's "no miss ends with unknown."
- **RW-7 No widening, no trust escalation.** No arm may write outside the
  candidate's scope (§11.1) or raise `trust_level` above what the source actor
  supports. `high_risk_arg` eligibility is never granted by an arm.
- **RW-8 Principal fidelity.** Every minted unit carries the *calling*
  principal's `actor_id` (and `agent_node_id` when agent-authored). An arm may
  never attribute a write to a different principal (§13.5).

**Per-arm contract.**

| Arm | Trigger | Primary operation | Must guarantee | Forbidden | Code state |
|---|---|---|---|---|---|
| `episodic_arm` | new raw episode admitted | append; near-dedup (§2.3); stamp tier-eligibility inputs (`last_recalled_at`, `observation_count`) | ground truth is never rewritten; the episode stays recoverable (invariant #1); it is **never superseded and never promoted away** | supersession, deletion, embedding drops (those are §13.3 and §10) | append BUILT; **tier scheduling ABSENT** (§13.0) |
| `knowledge_arm` | candidate carries a `subject_key` (§3.3) | close-generation + INSERT (§7.3a) | at most **one** open generation per `(tenant_id, subject_key)`; the superseded generation stays citable; contradiction produces an edge, never an overwrite | in-place mutation; deleting the prior belief generation | half-BUILT, kind hardcoded (`lib.rs:11512`, `:11616`) |
| `procedural_arm` | candidate strategy or failure pattern | mint at `procedure_candidate`; transition to `Validated` **only** on a recorded validation event | a unit reaches `Validated` **only** via `validation = {method ∈ replay\|deterministic, trials, wins}` meeting the §4.2 threshold **and** the adversarial high-risk-step assertion; the validation event is durable and replayable | promotion by corroboration count, by recall frequency, or by any LLM judgment | **write side ABSENT** — read gate at `lib.rs:8362` has nothing that can pass it |
| `belief_arm` | low-trust observation | mint `belief`; evaluate §5 independence; promote to semantic when cleared | source-independent corroboration; confidence recomputed, never incrementally mutated (§5.1a) | promotion from K mutually-reinforcing same-origin observations | BUILT (§5) |
| `resource_arm` | pointer/blob/chunk | extractor state machine (§6.1) | ACL narrowing is applied in-stage; chunk identity stays `kind='resource'` | a parallel chunk table or a second embedding grain | BUILT (§6.1) |
| `preference_arm` | user-declared or correction-derived standing constraint | supersede the current chain head; the new head is hot-plane resident | exactly **one** chain head per `(tenant_id, data_subject_id, scope_id, preference_key)`; superseded preferences stay citable; the head is injected deterministically, not retrieved competitively | decay; corroboration gating; minting by an unprivileged principal (§13.5) | **supersession BUILT** (`ffa640b8`, regression-tested end-to-end through Postgres: `supersedes`/`contradicts` edges minted, retired generation closed and excluded from recall). RW-7 actor gating BUILT. **Hot-plane injection still ABSENT** — the head is still retrieved competitively, because §13.3 tiering does not exist |
| `working_state_arm` | agent's own retained reasoning / plan state | append, then **compact-and-supersede** at the scope budget | never crosses `agent_node`; never promoted to any other kind; never counts as corroboration; TTL-bounded | truncation-by-discard; cross-principal read; `high_risk_arg` eligibility | **ABSENT** — kind unminted |

**`chain_head` is derived, not a column.** The report's "chain-head" is exactly
§7.3a's open generation:
`chain_head(k) = the unique unit with kind='preference', payload->>'preference_key' = k,
transaction_to IS NULL, state='active'`. The partial-unique index that already
enforces "at most one open generation per subject" (§7) enforces the chain head
for free. **Do not add a `chain_head` column** — it would be a denormalized
cache of a uniqueness constraint we already hold, and the second source of truth
is how the store-divergence class of bug enters.

### 13.2 Extending `MemoryKind` (policy rows written here; minting is W3.2)

The kind enum is **frozen-but-extensible** (`00` §2): a new kind is a governed
additive migration (`25` §11c), and `00` §4 requires `04`, `05`, `06`, `08`, and
`20` to be updated in the same change that mints one. **This section writes the
policy rows and the admission test; it does not mint the kinds.** Until the
migration lands, `MemoryKind` remains the five variants at
`crates/memphant-types/src/lib.rs:968`, and anything below marked *unminted* is
unrepresentable in code.

Admission test for any new kind is unchanged (§0.1): it must fill a **distinct
row on every policy column**, or it is rejected.

#### 13.2a `preference` (MINTED 2026-07-31)

| Column | Value |
|---|---|
| Write path | user-declared constraint, or a constraint derived from an explicit user correction; `actor_id` must be the data subject or a `trusted_user` acting for them |
| Promotion | **none.** A preference is declared, not promoted. It is superseded or revoked, never corroborated into existence |
| Trust | actor-gated at write; the *content* is always data (never control flow, never `high_risk_arg`-eligible) |
| Retrieval default | **deterministic hot-plane injection of the chain head** — not competitive retrieval. This is the one kind whose recall path is assembly, not ranking |
| Decay | **none.** A preference does not fade with time; it ends by supersession or revocation. This is the column that most sharply separates it from `semantic` |
| Main risk | over-personalization — which is precisely what the `27` §1 restraint launch gate measures |

Distinct from `semantic` on promotion, retrieval, and decay; distinct from
`belief` on trust and promotion. It passes §0.1.

*Why it blocked work, and what unblocking it did NOT buy.* Track U's 51
preference goldens
(`docs/build-log/2026-07-30-phase1-golden-banks-and-retrieval-probe.md`) had no
kind to be stored as. They do now. But minting the kind moved **no score**:
on MemoryCode, with the router landed and the harness unchanged,
latest-state-wins is 0.3123 — bit-identical to the pre-router measurement —
because the *first* gate is an explicit subject, and nothing on the served
write path produces one for a restated convention at $0
(`docs/build-log/2026-07-31-preference-writepath.md`). A body-derived key was
measured and recovers 8 of 1063 gold groups. Making a mechanism reachable is
not the same as making it fire.

#### 13.2b `working_state` (PROPOSED — unminted)

The agent's own retained reasoning and plan state — *why* it chose what it
chose, not *what* happened. `episodic` records the latter; nothing records the
former.

*Evidence this is load-bearing.* OpenAI reports GPT-5.6 Sol moving **13.3% →
38.3% RHAE on the ARC-AGI-3 public set with the model unchanged**, purely from
two context-management settings: retaining the model's private reasoning across
turns instead of discarding it, and **compacting rather than truncating** when
the window fills. **Evidence handling is binding**: that number is
OpenAI-self-run on their own harness and is not an ARC Prize leaderboard result.
It is citable **only** as evidence about harnesses — the 13.3→38.3 delta — and
**never** as a cross-model comparison (`27` §1's reproduced-baseline rule and
the same-lattice rule both forbid it). What it establishes for us is narrow and
sufficient: prior plan state is memory, and discarding it is a measurable loss.

| Column | Value |
|---|---|
| Write path | agent-authored only; `agent_node_id` **required** (not nullable for this kind) |
| Promotion | **never.** Working state does not become semantic fact. If a plan yields a durable conclusion, that conclusion is written as an episode and extracted normally — so it passes the corroboration gate like anything else |
| Trust | floor trust, self-authored; **never counts as corroboration** (§5 independence gate ignores it, same rule as the pinned block §12); never `high_risk_arg`-eligible |
| Retrieval default | same `(scope_id, agent_node_id)` only. **Never inherited, never granted** — it is a protected category (`06` §2.1), i.e. it carries no `scope_policy` `inherit` row, so isolation falls out of the schema rather than out of special-case code |
| Decay | TTL-expire on session close, or compaction (below). Not DSR — there is no review signal on a plan |
| Main risk | **self-reinforcing hallucination**: an agent that retains its own wrong plan re-reads it as evidence next turn. This is a new risk row, not a variant of poisoning, because the adversary is the principal itself |

**Compaction, and the constraint it collides with.** At the per-scope
working-state budget the arm must **summarize-and-supersede**, not discard the
oldest — that is the substitution that paid in the ARC-AGI-3 harness, and it is
structurally the same failure mode as our packer's hard drop under a cap. But
summarization is an LLM call, and RW-2 forbids LLM calls in the router. The
reconciliation available without breaking anything: compaction runs as a
**`reflect` stage-1 extraction job** (where LLM cost already lives), and the
router arm only consumes its output. Whether that is sufficient, or whether
compaction belongs at compose time instead (where `05` places it, and where it
becomes a latency line on every recall) is **OPEN-1** below.

#### 13.2c `knowledge` — a name, not a new kind (with the open question stated)

The campaign report's four router types are *knowledge / episodic / procedural /
preference*. Two of those four were called unnameable. That is exactly right for
`preference`. It is **not** right for `knowledge`, and this spec declines to
invent a resolution that its own §0.1 forbids:

- "knowledge = supersession" is a description of what `MemoryKind::Semantic`
  already is (§3, §7.3a). Run it through the §0.1 admission test and it fills the
  *same* row as `semantic` on write, promotion, trust, retrieval, decay, and
  risk. A sixth variant with an identical policy row is precisely what §0.1
  rejects, and it would fork the bitemporal supersession contract across two
  kinds — the most expensive possible duplication.
- Therefore, in this spec, **`knowledge` is the router-arm name for
  `MemoryKind::Semantic`** (`knowledge_arm` in §13.1). No enum variant is added.
- **OPEN-2** records the live alternative — renaming the variant — and its cost.

*Correction to the W3.2 brief, stated plainly so no reader is misled:* W3.2 asks
for `Preference` **and** `Knowledge`. This spec supplies the first as a real kind
and the second as an arm name, on the §0.1 grounds above. If the owner wants the
rename instead, it is OPEN-2, not a silent decision.

### 13.3 Retention tier as state, with real transitions

§2.4 froze the tier vocabulary and named a job. It never said **who writes the
column, on what predicate, or what the reader does with it** — which is why the
column has shipped with zero writers and zero readers since
`20260703_001_wsa_bootstrap.sql:278`. This section supplies the missing half.

**Tier is a property of an `episode`, never of a `memory_unit`.** Units have
`state` (§7.1); episodes have `retention_tier`. Conflating them is the first
implementation error to avoid.

**Transition table.** `T_*` and `W_*` are tenant-configurable policy parameters,
not constants in code.

| From | To | Predicate | Writer | Physical effect | Reversible |
|---|---|---|---|---|---|
| `hot` | `warm` | `age ≥ T_warm` **and** DSR retrievability (§8) of every derived unit `< R_warm` **and** no citing recall within `W_warm` | `tier_episode` (`14` §3.3) | raw blob → object store; derived units **and** embeddings retained in Postgres | yes |
| `warm` | `cold` | no citing recall within `W_cold` | `tier_episode` | blob compressed; **derived embeddings dropped, units kept as stubs** | yes |
| `warm`/`cold` | `hot` | any recall that cites the episode | the recall path **enqueues** re-promotion; the job performs it | embeddings re-derived from the recoverable raw blob | — |
| any | any | a `forget` | — | **not a tier transition.** Deletion is §10 / §13.4 and never moves this column | n/a |

**Tier invariants.**

- **RT-1 Recall never blocks on a tier.** Stages 1–6 run on Postgres rows only
  (`02` §6). A cold blob is fetched at Stage 7 only when a citeable unit needs a
  raw snippet, time-bounded; on miss or timeout the pack ships the unit and its
  citation with **no snippet, labeled `evidence_cold`**, and enqueues
  re-promotion. Degraded, never failed.
- **RT-2 Nothing unrecoverable is dropped.** Every transition destroys only
  material re-derivable from the raw episode. This is what invariant #1's word
  "recoverable" was always carrying.
- **RT-3 Exactly one writer.** `tier_episode` is the only code path that writes
  `retention_tier`; the recall path may only *enqueue*. This is directly
  falsifiable: any write to `retention_tier` outside that job is a defect, and
  the test is a grep.
- **RT-4 No silent transitions.** Every transition is recorded in the job trace
  (`14` §3.1) with the predicate values that fired it.
- **RT-5 Idempotence.** Two `tier_episode` runs over the same episode in the
  same policy window produce one transition (`14` §3 job key).
- **RT-6 Tier is not access control.** A cold episode is exactly as authorized
  as a hot one. Tier changes latency and channel availability, never who may
  read.

**Conformance tests an implementer writes against this section.**

1. An episode with no citing recall past `W_warm` is `warm` after one
   `tier_episode` run, and `hot` again after one citing recall plus one run.
2. A `cold` episode's citations still resolve (`06` deletion-completeness suite
   must distinguish "cold" from "deleted" — they are different outcomes).
3. Re-promotion re-derives embeddings that match a pinned embedding profile.
4. Running `tier_episode` twice is a no-op the second time.
5. `grep -rn 'retention_tier' crates --include='*.rs'` returns writers only in
   the `tier_episode` implementation, and readers only in the Stage-7 cold-fetch
   path. (Today it returns **zero** of either.)

**Open:** what `hot` means for *units*, as opposed to episodes — the report's
"hot plane (≤1k tokens, always injected)" is an assembly budget, not a storage
tier. This spec places always-injected content in exactly two places: the pinned
scope block (§12) and the preference chain heads (§13.2a). Whether a third
hot-plane slot exists is **OPEN-3**.

### 13.4 Demotion, not deletion — and how it differs from tombstoning

These are two orthogonal mechanisms that the campaign report ran together and
that the code has only one of.

| | **Demotion** (specced here, unbuilt) | **Deletion** (§10, `06` §6 — built) |
|---|---|---|
| Motivation | cost and relevance | privacy and legal |
| Trigger | staleness, interference, deep supersession | user or legal `forget` request |
| Reversible | **yes, fully** | **no, by design** |
| Content | retained and recoverable | removed or crypto-shredded; tombstone retained |
| Default recall | absent | absent |
| Exhaustive recall | **present** | absent |
| Audit | job trace + a recall-trace drop label | `deletion_generation` + the release-blocking completeness check (`14` §4.1) |
| Code today | nothing (`demote`/`demotion` → 0 hits in `crates/`) | `subject_tombstone` + forget path (`store.rs:1325`, `:1628`) |

**The one-line difference:** deletion removes the content and keeps a marker;
demotion keeps the content and removes it from the fast path. A demotion that
cannot be undone by one sufficiently good recall is a deletion with extra steps,
and is a contract violation.

**Unit-level demotion contract** (episode-level demotion is §13.3's tiering):

- **DM-1 Reversible by evidence.** One recall in which the unit scores above the
  re-promotion threshold restores it to the default pool. No manual step.
- **DM-2 Bitemporally inert.** Demotion does **not** close a generation, does
  not set `transaction_to`, does not change `state`. What MemPhant believes is
  unchanged; only what it *reaches first* changes. (This is the invariant that
  keeps demotion out of the §7.3a audit chain, where it does not belong.)
- **DM-3 Exemptions are structural.** A `preference` chain head (§13.2a) and the
  pinned scope block (§12) are **never** demotable. These are the two
  guaranteed-presence surfaces; demoting them silently breaks a promise the
  product makes.
- **DM-4 Visible in the trace.** A demoted unit that would otherwise have been a
  candidate appears in the recall trace with a demotion drop reason and the
  signal that demoted it. Otherwise `27` §0's "no miss ends with unknown" fails
  on exactly the misses demotion causes.
- **DM-5 Exhaustive mode reaches it.** `recall(..., exhaustive)` searches the
  demoted pool. Non-negotiable — it is what makes DM-1 checkable.

**The storage mechanism is OPEN-4.** Three candidates, deliberately not resolved
here because the deciding evidence is a measurement we have not run:

1. a new `recall_tier` column on `memory_unit` — the only option that makes
   demotion an *auditable event* with a timestamp and a cause;
2. a new `demoted` value in `UnitState` — cheapest to filter, but it collides
   with §7.1's meaning of `state` (belief maturity) and with DM-2;
3. a pure **recall-time predicate** over signals that already exist (DSR
   retrievability §8 + supersession depth + last-citation age) — no migration,
   no new state, and closest to what the code already computes at `lib.rs:7364`.

(3) is the KISS default and is what this spec would ship first; (1) is the only
one that satisfies DM-4 fully. Deciding between them on taste is the error. See
OPEN-4.

### 13.5 Principal-scoped multi-agent access (what CaaS needs)

The substrate already has the isolation primitives: the scope tree and
`scope_policy` (§11.0), `agent_node.level` (§11), per-kind isolation levels
(§11.2), and API-key principal binding. What the governance core adds is the
*attribution and sharing rules across kinds* for one user with many agents.

**Principal.** A principal is the tuple
`(tenant_id, data_subject_id, actor_id, agent_node_id?)`. `actor_id` is the
provenance axis, `agent_node_id` the access-tree axis (`00` §2) — a human user
has the former and not the latter; an agent has both.

- **PS-1 Attribution is not optional.** RW-8: every unit records the writing
  principal; no arm may attribute a write to another principal. Impersonation is
  a write-path bug, not a policy question.
- **PS-2 Preferences belong to the *subject*, not the agent.** A preference
  written by agent A is a preference of the user, and is visible to agent B in
  the same scope subtree subject to `scope_policy`. This is the whole "many
  agents, one memory" promise; if preferences were per-agent, the product would
  not exist.
- **PS-3 Working state belongs to the *agent*, always.** Per §13.2b it is a
  protected category with no `inherit` row and no grantable path. This is the
  contamination guard, and it is structural (no policy row can express the
  sharing) rather than enforced by a check that can be forgotten.
- **PS-4 Contamination is measured, not asserted.** Cross-agent leak rate and
  scope-leak rate are scored axes; the instrument is GateMem (`27` §1), which
  gates nothing until it is reproduced in our own harness.
- **PS-5 The database-level guarantee is currently absent, and this spec does
  not pretend otherwise.** Isolation on the served path rests **entirely on
  application predicates**: `SET ROLE` appears **0 times outside `tests/`**, no
  login role is a member of `memphant_app`, the roles are `NOINHERIT`, and there
  is no startup assertion that the connected role is non-superuser. Landing
  `memphant_app` (W3.3) is a prerequisite for any claim that PS-1..PS-3 are
  enforced in depth rather than in application code.

### 13.6 Specced-but-unbuilt register (§13's honesty marker)

Read this before citing anything above. `STATUS.md` records state; this table
records the **spec-to-code gap** for the governance core specifically.

| §13 contract | Code state | What it needs (workstream) |
|---|---|---|
| Typed write-router, RW-1..RW-8 | **PARTIAL (2026-07-31, `ffa640b8`).** RW-1 kind-totality, RW-3 own-kind, RW-4 append-only and RW-7 no-escalation hold via `write_router_arm`/`supersedes_own_kind`. The stage is still one loop with an `admission_hint` ladder inside the arm-gated branch, so the full `WriteDecision` restructure is NOT done | finish `reflect` stage 4 |
| `knowledge_arm` dispatch (not a literal) | **BUILT** — the literal is gone; the filter comes from the arm | — |
| `preference` kind + `preference_arm` | **kind MINTED, supersession BUILT** (`20260731_006`, `ffa640b8`). Hot-plane chain-head injection ABSENT (needs §13.3). **And reachability is not the constraint that binds**: nothing on the served write path produces a subject key for a restated convention at $0 (`docs/build-log/2026-07-31-preference-writepath.md`) | a mutation-time control-plane hook on the supersede path; the `supersedes_own_kind` seam is left open for it |
| `working_state` kind + arm + compaction | **UNBUILT, unnameable** | W3.2; OPEN-1 first |
| Procedural write-side promoter to `Validated` | **UNBUILT** — read gate has nothing to admit (`lib.rs:8362`) | W3.5 |
| `retention_tier` writer + reader, `tier_episode` | **UNBUILT** — 0 readers, 0 writers | W3.4 |
| Unit-level demotion | **UNBUILT** — 0 hits for `demote` in `crates/` | OPEN-4 first |
| Exhaustive recall over demoted units | **UNBUILT** | follows demotion |
| Principal attribution in depth (`memphant_app`) | **UNBUILT** — RLS decorative on the served path | W3.3 |
| Episodic append, belief corroboration, resource extractor, bitemporal generations | **BUILT** | — |

### 13.7 Open decisions (deliberately unresolved)

Recorded as open rather than resolved by invention. Each names the evidence that
would close it, so none of them closes on argument alone.

- **OPEN-1 — where compaction runs.** `reflect` stage 1 (LLM cost already lives
  there; keeps RW-2 intact; but compaction then lags the turn that needed it) vs
  compose time (`05`; fresh, but adds an LLM call to the recall latency budget
  and competes with our own retrieve-instead-of-summarize thesis). *Closes on:* a
  paired measurement of compacted vs truncated working state on a lane where
  retrieval is held constant.
- **OPEN-2 — `knowledge` as a rename of `Semantic`.** §13.2c makes it an arm
  name. The alternative is renaming the enum variant, which is a breaking schema
  change (`25` §11c) touching `04`/`05`/`06`/`08`/`20`, every stored `kind`
  string, and both SDKs, for a naming gain. *Closes on:* an owner decision that
  the vocabulary gain is worth a breaking migration. Default if nobody decides:
  keep `semantic`.
- **OPEN-3 — whether a third always-injected hot slot exists** beyond the pinned
  block (§12) and preference chain heads (§13.2a). The B1 observation-block
  experiment already failed its gate at paired delta 0.000 and was deleted
  (`STATUS.md`, 2026-07-22), which is evidence against, not for. *Closes on:* a
  preregistered paired reader gate, same shape as B1's.
- **OPEN-4 — the unit-demotion mechanism** (`recall_tier` column vs a `demoted`
  `UnitState` vs a pure recall-time predicate; §13.4). *Closes on:* a ForgetEval
  + restraint-gate run comparing the recall-time predicate against a stored tier
  — specifically, whether demotion needs an auditable event or only an effect.
- **OPEN-5 — whether cold episodes may lose the dense channel.** §13.3 drops
  embeddings at `cold`, so a cold episode's units are reachable only by
  exact/lexical/edge channels until re-promotion. *Closes on:* measuring recall
  loss on a corpus with a realistic cold fraction. Until then, do not assume
  RT-1's "degraded, never failed" covers dense-channel loss — it covers snippet
  loss.
- **OPEN-6 — whether a sub-agent may write a user preference at all**, or only
  propose one for confirmation. PS-2 makes preferences subject-scoped, which
  makes an agent-written preference immediately visible to every sibling agent.
  *Closes on:* Track U scoring with both policies once the `preference` kind
  exists.
