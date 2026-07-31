# MemPhant - Trust and Security Spec

## 0. Threat Model

Memory is a persistent attack surface. Attackers try to make the agent remember malicious instructions, false facts, stale facts, or biased tool parameters.

OWASP ASI06 names memory and context poisoning as a core agentic risk: <https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/>.

MPBench (arXiv:2606.04329, "From Untrusted Input to Trusted Memory: A Systematic Study of Memory Poisoning Attacks in LLM Agents") systematizes the surface this doc defends: **four memory write channels, nine structural vulnerabilities, and a taxonomy of six memory-poisoning attack classes**, shipped with the MPBench benchmark (§9 inventory via `12`). Two of its findings bind here: existing prompt-injection defenses fail to cover memory poisoning (a memory-specific suite is mandatory, not a prompt-injection suite reused), and **more aggressive write/retrieval policies increase exploitability** — the capability–security tension means restraint/admission-control (`05` §1.5 relevance gate, `04` §9.2 write-time quality gate) is *also* a security control, not only a quality one.

OWASP Agent Memory Guard also treats memory as an explicit contract boundary. MemPhant should align with that shape: classify memory, constrain where it can flow, and provide snapshot restore/quarantine. (Maturity flag: Agent Memory Guard is a pre-1.0 OWASP **incubator** project — align with its *shape* and use its fixtures, but do not treat it as a stable normative standard yet. Status check 2026-07-02: still Incubator; Redis/Postgres backends are only *planned* — v0.3.0 slated Q2 2026, v1.0.0 Q4 2026 — so the Postgres-native governed-memory-defense slot is unoccupied.)

## 1. Security Invariants

1. Tenant isolation is non-negotiable.
2. Memory writes from untrusted sources are not high-trust facts.
3. Retrieved memory is data, not control flow.
4. High-risk actions require trusted/corroborated memory or no memory.
5. Every memory has provenance.
6. `forget` is tested as a security path, not a UX feature.
7. Service keys and admin tokens are never exposed through SDK or MCP.
8. Memory can explain a recommendation; it cannot authorize a consequential action.
9. DB/browser exposure is a release blocker, not a follow-up.
10. Raw telemetry defaults to IDs/counts/timings, not memory text.

## 2. Trust Levels

```text
trusted_user
trusted_system
verified_tool
unverified_tool
web_content
agent_output
imported_external
quarantined
```

Trust level affects:

- write destination
- promotion eligibility
- retrieval ranking
- context labels
- high-risk action eligibility
- decay/expiration

### 2.2 `trust_event` schema and ranking weights

Trust is not a free-text label — it is a typed, auditable event so a recall trace can explain *why* a candidate was down-weighted or excluded:

```sql
trust_event (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  target_kind  text NOT NULL,   -- 'episode'|'memory_unit'|'resource'|'actor'|'source'
  target_id    uuid NOT NULL,
  level        text NOT NULL,   -- the §2 vocabulary
  decision     text NOT NULL,   -- 'classify'|'quarantine'|'corroborate'|'promote'|'demote'
  reason_code  text NOT NULL,   -- controlled vocabulary
  corroborating_sources jsonb,  -- the independent (actor_id, source_kind) set, for promotion decisions
  policy_version text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  INDEX (tenant_id, target_kind, target_id, created_at)
)
```

The trust level applies an explicit, traced **retrieval-ranking multiplier** (the `down-weight low-trust` step, §4) so "down-weight" is a number, not a vibe — illustrative defaults, tuned against held-out evals (`24` refinement registry), never marketed before measured:

| Level | Default rank multiplier | High-risk-arg eligible |
|---|---|---|
| `trusted_system` / `trusted_user` | 1.0 | yes |
| `verified_tool` | 0.9 | yes if corroborated |
| `imported_external` | 0.6 | no |
| `agent_output` / `unverified_tool` | 0.4 | no |
| `web_content` | 0.3 | no |
| `quarantined` | 0 (excluded from default recall) | no |

**Composition (source ⊗ actor ⊗ corroboration → multiplier).** The table above is the *source-trust base*; the effective multiplier composes the three frozen trust-path inputs (`00` §2):

```text
effective = base(source_trust) × actor_factor(actor_trust) × corroboration_bonus
clamped to [0, base(source_trust)]      # NEVER raises a unit above its source ceiling
```

- **Source-trust is a ceiling, not a starting point.** Two independent `web_content` (0.3) sources agreeing produce a more-confident *belief*, never a `verified_tool` fact — the arithmetic enforcement of the `04` §5 independence rule (and the anti-corroboration-farming cap).
- **Actor trust is revocable:** an actor demoted after an incident (`trust_event decision=demote`) lowers `actor_factor` for *future* recalls without rewriting stored units — a compromised tool credential loses weight immediately.
- The composed value + its inputs are traced on the candidate record (`05` §3.2), so "why was this down-weighted" is reconstructable.

## 2.1 Protected Categories

Some memory is extra constrained even when trusted:

| Category | Default policy |
|---|---|
| credentials/secrets | never stored as recallable memory; secret refs only |
| payment/financial | semantic recall allowed only with high-trust sources and explicit purpose |
| medical/legal | high-trust source labels, freshness warnings, no autonomous action |
| personal identity | explicit subject scope and deletion support |
| high-risk tool args | suppressed unless corroborated and requested |
| child/private scope | no inheritance unless an explicit `scope_policy` `inherit` row admits the kind+level (`04` §11.0); protected kinds carry no `inherit` row (or an `admit=false` override) |

## 3. Write-Time Defense

On every write:

```text
classify source
scan for instruction-like content
assign trust level
store provenance
write raw episode/resource
quarantine suspicious memory units
require corroboration for semantic promotion
refuse to mint a preference below trusted_user/trusted_system
```

**`preference` is actor-gated at write** (`04` §13.2a, minted 2026-07-31). A
standing user constraint may only be declared by the data subject or a
`trusted_user` acting for them; a lower-trust caller's preference hint degrades
to a `belief` exactly as a `semantic` hint already does (`RW-7`). Its *content*
is always data — never control flow, never `high_risk_arg`-eligible — so a
preference cannot be used to widen scope or escalate trust.

No parser or LLM extractor is trusted enough to bypass provenance.

Anchoring defense at write/storage time is the survey consensus, not a house preference: the memory-lifecycle security survey behind Verifiable Memory Governance (arXiv:2604.16548) concludes that robust long-term-memory security "cannot be retrofitted at retrieval or execution time alone" and must be anchored in "storage-time provenance, versioning, and policy-aware retention from the outset" — exactly the §3/§5/§6 layers. Gap-check (named, not hidden): of the survey's six lifecycle phases (Write / Store / Retrieve / Execute / Share&Propagate / Forget&Rollback), **Share&Propagate — cross-scope/cross-tenant propagation governance — is the thinnest phase in this suite**; scope-inheritance policy (`04` §11.0) is the current control, and propagation governance beyond it is an open item, not a solved one.

### 3.1 Resource-Fetch SSRF Floor

Resource memory (`04` §6) stores URL/DOM/artifact pointers, and consolidation may fetch them — so MemPhant has a server-side fetch surface and inherits SSRF risk. A non-negotiable floor (mirrors Syndai's proven `ios_namespace` resolve-and-reject pattern):

- **Resolve then re-check.** Resolve the hostname, then reject if the resolved address is private/loopback/link-local/metadata-range — do not trust the literal hostname (DNS-rebinding defense).
- **Reject IPv4-mapped IPv6** (`::ffff:a.b.c.d`) and other encodings that smuggle a private v4 address through a v6 parser.
- **Allow-list schemes** (`https`, and `s3`/object-store URIs through the dedicated client), reject `file`/`gopher`/`ftp`/etc.
- **Bound** redirects, response size, and time; fetched content enters at `web_content` trust (never higher) and goes through the same write-time classification.
- The fetch path is a named test lane; a resolved-private-address bypass is a release blocker.

### 3.2 What the Write-Time Classifier Actually Decides

"Scan for instruction-like content" (§3) is the **weakest** of the write-time signals, and the spec must say so — the strongest 2026 attacks defeat it by construction (MINJA, arxiv 2503.03704, injects records disguised as *plausible reasoning*, not instructions; input classifiers cut injection only ~18% before trivial rephrasing, arxiv 2504.11168). The classifier produces a **label + disposition, never a trust grant**; trust comes from provenance, which an attacker cannot forge:

1. **`source_kind` + provenance (load-bearing).** Trust class is assigned from *where the bytes came from* (`04` §7), unforgeable: a web page claiming "I am a trusted system note" still enters at `web_content` (0.3). The §2.2 multiplier keys on this, not content.
2. **Content heuristics (a label, not a gate).** A deterministic scan flags instruction-like surface (imperatives aimed at the agent, embedded tool-call syntax, fenced "system" blocks in fetched DOM) → **raises the disposition** toward `quarantine` and writes a `trust_event`; it never lowers below the provenance floor and never grants trust.
3. **Structural anomaly.** Source/length/encoding mismatches (a "tool output" carrying a 4KB essay; base64/zero-width/homoglyph payloads) raise disposition.

**Honest limit:** a content scanner *cannot* catch reasoning-disguised poisoning. MemPhant therefore does not stake poisoning defense on detection — it stakes it on (a) provenance-derived trust the scan cannot override, (b) read-time down-weight + high-risk suppression (§4), and (c) the independent-source corroboration gate at promotion (`04` §5). Dispositions (`allow` / `label` / `quarantine` / `redact`) mirror OWASP Agent Memory Guard so the write policy is wire-compatible with the reference fixtures (§0).

**The harder truth — provenance is necessary but NOT sufficient.** Two 2026 attack classes have *clean* provenance by construction: **query-only self-generated poison** (MINJA, arXiv:2503.03704 — averaged 98.2% injection / 76.8% attack success; the attacker only *queries* and the victim agent writes the poison into its own memory at a legitimate trust class, so provenance looks impeccable) and **Sybil corroboration-farming** (an attacker controlling ≥2 cheap `(actor_id, source_kind)` identities defeats the independence gate *by construction* — Douceur's Sybil result). Environment-only injection with clean provenance is also real (eTAMP, arXiv:2604.02623). So (a)–(c) bound *blast radius* but cannot *detect* legitimately-provenanced poison — MemPhant adds a behavioral/structural anomaly layer (§4.3) and an explicit Sybil-resistance assumption on `actor_id`.

**Certified validation of the write-side layer + a named high-stakes recall candidate (SMSR, arXiv:2606.12703 — single-author preprint; weigh accordingly).** SMSR names Multi-Session Memory Poisoning (poison planted through normal interactions that affects *future* users) and certifies the defense split this section stakes out. Its write-side result: HMAC-SHA256 provenance signing on writes drove unsigned-injection success from 93–100% to 0% across 15 enterprise scenarios — authenticated, signed provenance at write time is a certified defense layer, not hygiene. Its read-side component, **randomized memory ablation + majority voting** (k parallel recalls over randomized memory subsets, vote on the answer; held authenticated single-record injection to 8.0% success, 95% CI [5.8, 10.9], n=450, and cut a query-only attack from 65.3% to 5.3%), is a **named candidate for the explicit `deep`/high-stakes recall mode only** (`05` §1.3) — never the hot path (k× read cost; SMSR's own utility numbers are 90% provenance-only vs 85% combined) and adopted only if an ablation arm (`05` §9) earns it.

### 3.3 Quarantine Lifecycle

Quarantine is "suspicious but not provably malicious." A small state machine on `04` §7.1 (`quarantined → candidate | expired`); **every transition writes a `trust_event`**:

- **Entry** — a `quarantine` disposition or a read-time poisoning signal; written at `quarantined` (0 multiplier, excluded from default recall) with `body` retained for inspection. Never silently dropped (silent drop loses the forensic + corroboration trail).
- **Inspection** — recallable only via `include_quarantined: true` by an `analyst`/admin role (`25` §3); never enters default context.
- **Release** — to `candidate` requires either an explicit admin actor with a `reason_code`, **or** the standard belief→semantic path: re-corroboration by ≥2 **independent** sources (`04` §5). A quarantined record cannot be laundered into trust more cheaply than an ordinary belief.
- **No self-release.** An `agent_output`/`web_content` actor can never release its own quarantined content — this blocks the attacker-controlled-channel self-promotion path.
- **Expiry** — not released within a tenant window → `expired` (auditable, never default-recallable). Quarantine is bounded, not a storage tier.

## 4. Read-Time Defense

On every recall:

```text
apply tenant and scope constraints
apply privacy generation constraints
exclude quarantined by default
down-weight low-trust candidates
label provisional evidence in context
block low-trust memory for high-risk governed/tool actions
apply per-resource ACL (04 §6.1): chunk recall also requires the parent resource.acl scope/trust/protected clauses — the chunk's own scope_id passing is necessary, not sufficient
emit trace
```

### 4.1 Double Gate

There are two independent gates:

1. Tool/prompt availability: the runtime decides whether recall is available to an actor.
2. Memory policy: MemPhant decides what that actor can retrieve.

Both gates must pass. The API must not assume that hiding a tool in a prompt is access control.

### 4.2 Suppression Labels: The Enforceable Contract

Invariant #8 ("memory can explain, not authorize") only holds if the runtime can *mechanically* tell which evidence is ineligible for a consequential parameter. Every recalled item (`08` §4) therefore carries a typed eligibility label, not prose:

```jsonc
{ "id": "mem_...", "trust": "agent_output", "score": 0.41,
  "eligibility": { "high_risk_arg": false, "citable_fact": false,
                   "reason": "below_trust_floor" } }   // below_trust_floor|uncorroborated|quarantined|stale|protected_category
```

- **`high_risk_arg` is computed, not advisory** — `true` only when trust tier is high-risk-eligible (§2.2) **and** (for `verified_tool`) corroboration holds. The runtime's governed-action layer **must** filter on it before deriving any money/trading/procurement/hiring/tool parameter (invariant #4); a runtime that ignores the label is what the `high-risk action suppression` eval (`05` §10) catches.
- **"High-risk" is a runtime-declared parameter class, not MemPhant's guess.** When the recall request carries `arg_risk: high` (or the Syndai governed-action spec tags the call site), MemPhant **hard-excludes** `high_risk_arg:false` items from the pack and lists them in `dropped[]` with `reason: trust` — suppression becomes a server-side guarantee. Absent the hint, items ship labeled and suppression is the runtime's gate-1 duty.
- The label is part of the OpenAPI/MCP schema (`08`) — there is no "raw" recall that returns trust-blind items.

### 4.3 Behavioral/Structural Anomaly Layer (because provenance is not enough)

Provenance + corroboration + read-time down-weight (§3–§4) cannot catch poison with legitimate provenance (§3.2). So a **post-hoc, content/effect-level audit** runs in the `reflect` loop (never the hot path), complementing — not replacing — the online gates:

- **Causal attribution + structural consistency** (the MemAudit pattern, arXiv:2605.23723, which cut MINJA attack success ~70%→0% in the QA setting): score each memory's *counterfactual influence* on agent outcomes and flag records that sit anomalously in the memory-consistency graph — a fact the agent keeps acting on but that contradicts the corroborated majority, or one whose insertion correlates with a behavior shift. High-anomaly records are **quarantined (§3.3) for re-corroboration, never auto-deleted**. This is **forensic, not an admission gate** — it catches what cleanly-provenanced poison slips past the write-time classifier.
- **Sybil-resistance is an explicit assumption, not a hope.** The independence gate (`04` §5) is load-bearing only if `actor_id` is **costly/attested** — a corroboration count over cheap, freely-mintable identities certifies nothing (Douceur). Independent-source promotion therefore requires actors whose identity carried a real cost (an authenticated user; a `verified_tool` with a provenance attestation), and a burst of new low-cost actors all corroborating one belief is itself an anomaly signal, never a promotion.
- **Dual-guard + high-risk quorum.** Injection/anomaly filtering runs at **both** write-time (disposition, §3.2) **and** read-time (the relevance/suppression gate, §4.2 + `05` §1.5); a high-risk read (one that will fill a money/tool argument, invariant #4) additionally requires a **quorum** of independent corroborating memories and may **randomize** which corroborating set it draws, so a single planted record cannot deterministically steer the action.

**Honesty boundary** (kept from §0 / `17` §4): this *reduces* poisoning success against query-only and Sybil attacks; it does not make MemPhant poisoning-proof. The named evals (`05` §10 + the MINJA / eTAMP red-team fixtures) measure the residual, and every new attack class becomes a permanent red-team fixture (`09` §5).

## 5. Provenance

Every memory unit links to one or more:

- episode
- resource
- actor
- agent node
- source URI
- import batch
- extraction job
- trust event

Citations are not decorative. They are the enforcement path for hallucinated memory references.

## 6. Forget and Delete

`forget` must invalidate:

- memory units
- edges
- embeddings
- citations
- retrieval cache rows
- derived consolidated memories
- resource chunks
- cold blobs according to retention policy

Hard delete vs tombstone is policy-driven. The API must tell callers which policy applied.

### 6.2 Deletion Completeness: The Adversarial Argument

`forget` is tested as an attack (invariant #6), so completeness is an *enumerated-path proof*, not "we deleted the row." A forgotten unit must be unreachable through **every** path, each with a distinct resurrection hazard:

| Path | Resurrection hazard | Control |
|---|---|---|
| Vector / HNSW | lazy delete leaves the node's graph *neighbors* reachable; pgvector HNSW does not compact on delete | `deletion_generation` query filter **+ scheduled compaction/reindex** with an SLA; eval queries by embedding-nearest after delete |
| Lexical / FTS | tsvector row survives a soft delete | generation filter; tombstone excluded from `tsquery` |
| Retrieval cache | a cached result still names the unit | cache rows carry `deletion_generation`; forget bumps it and invalidates |
| Derived / consolidated | a semantic unit *derived from* the forgotten episode still cites it | citation-graph cascade to derived units (`04` §7.2) |
| Cold blob / export | raw blob in object store, or a prior export | retention-policy delete/tombstone; **exports are out-of-band and documented as such** |
| Edge | a `contradicts`/`supersedes` edge re-surfaces it via expansion | edge invalidation on the generation bump |

**Honesty boundary:** MemPhant deletes from its *own* recall paths and proves it; it **cannot** claim erasure from a downstream model fine-tuned on exported memory, and the API/docs say so (2026 unlearning consensus: do not promise "complete deletion" when only the retrieval source is deletable, arxiv 2410.15267). `forget` returns which policy applied so the caller makes its own attestation. The `deletion completeness` eval (`05` §10) *attacks* recall from every channel above and asserts zero leakage.

**Erasure manifest — enrolled-or-exempt-with-reason (Syndai-production-proven).** The enumerated-path table covers *channels*; the manifest covers *tables*: every user-scoped table is either enrolled in the ordered erasure tuple or explicitly exempted with a stated reason, and a contract test walks the live schema so a newly added user-scoped table **cannot silently escape deletion** — the test fails until the table is enrolled or exempted. One deliberate carve-out: the deletion-generation registry itself **survives the purge** (exempt-with-reason), because it is what rejects stale queued jobs that still reference the erased subject — deleting the ledger that proves deletion would let queued work resurrect activity against the purged data.

**Cross-store atomicity (the orphaning failure) + scale.** Deletion spans Postgres rows, the vector index, edges, the cache, and the object store — and the production failure is a *partial* delete: Mem0 #3245 shows `delete()` removing vector-store data while leaving orphaned graph nodes that accumulate forever. So `forget` runs as a **saga with read-back verification**, not a fire-and-forget fan-out: each store's deletion is a saga step, the `deletion_generation` bump is the durable commit point, and a **read-back** (re-query each store for the tombstoned id) confirms the step before the saga completes; an incomplete step is release-blocking and retried, never silently dropped. At 100K+ units this runs as a bounded async background job (the consolidation backpressure model, `02` §3.1), not inline. Blob deletes the saga can't complete inline (a crash mid-saga) are reconciled by `blob_gc_sweep` (`14` §4): the `deletion_generation` bump is the durable commit point, blob deletes run after it as saga steps, so a crash leaves a `MIN_AGE`-grace-collectible orphan — never a live recall path to a deleted blob, never a missing blob under a live row — and a dedup-shared blob is collected only when **zero** live rows reference the hash (`02` §2.3).

**"Forget user X" without a full index rebuild — crypto-shredding.** HNSW does not truly delete (it tombstones; a naive delete leaves a recoverable vector until compaction), and GDPR Art. 17 erasure must reach indexes *and* backups, not just the primary table ("archiving to cold storage is not erasure"). Tombstone compaction is therefore part of the deletion guarantee (the scheduled reindex with an SLA — `14` §4.1), not housekeeping. For "forget everything about user X," the substrate may **crypto-shred** by destroying that user's **per-user DEK** (§6.1.1#5 — the per-tenant KEK that wraps it shreds the *whole* tenant; a per-tenant key alone cannot erase one user). Crypto-shred covers the **encrypted bodies/blobs only**: destroying the DEK renders all of that user's `episode.body`/`memory_unit.body`/blobs (and any backups of that ciphertext) "computationally intractable" instantly, no index rebuild (MemTrust, arXiv:2601.07004). The indexed **vector is plaintext** (HNSW cannot read ciphertext — distance is not preserved under encryption, arXiv:2508.10373 / `02` §2.1a), so it is removed by **tombstone + scheduled compaction** (`14` §4.1), not by key destruction — and **erasure is INCOMPLETE until the vectors are physically compacted out of the index. The tombstone *filter* alone is not erasure**, because a plaintext embedding left in the HNSW graph is invertible back to the original PII text, and 2026 inversion is now **cross-model, black-box, and training-free** (Vec2Text recovers 92% of a 32-token input, arXiv:2310.06816; Song & Raghunathan recover 50–70% of words, arXiv:2004.00053; ALGEN arXiv:2502.11308 and Zero2Text arXiv:2602.01757 invert black-box across models). **The index is the deletion boundary.** **Order:** (1) destroy the DEK (durable erasure commit, `deletion_generation` bump), (2) run the deletion saga + `blob_gc_sweep` to tombstone the rows, (3) the scheduled reindex/compaction (`14` §4.1) **physically removes the user's vectors** — the tombstone→compaction window is bounded by an SLA, and "forget user X" is not satisfied until step 3 completes. Caveat: crypto-shred is *record-present-but-unreadable*, contingent on the key being truly unrecoverable (KMS/TEE) and no harvest-now-decrypt-later break — it **complements** vector compaction, never replaces it. **GDPR framing is hedged:** key-destruction-with-encryption is **pseudonymisation** (Art. 29 WP216), which regulators treat as rendering data unusable "at least until the algorithm is broken" (EDPB) — *not* settled unconditional anonymisation; MemPhant claims "reduces recoverability", never "provably erased" (mirroring `17` §4 scope honesty). (A max-security profile may also encrypt the vector — but only on `index_strategy=exact`, scan-only, no HNSW; `02` §2.1a.) **Crypto-shred is correct across every restore point:** destroying the per-user key renders the ciphertext intractable in all backups simultaneously (GDPR Art. 17's "erasure must reach backups"), so a restore *cannot* resurrect shredded memory — the cross-store reconciliation sweep (`14` §4.2) classifies a shred-missing blob as expected (not a failure) and tombstones the now-unreadable row to preserve invariant #1.

## 6.1 DB Exposure Gate

Hosted MemPhant does not expose memory tables through browser roles or PostgREST-style public APIs. **RLS is nonetheless the default on hosted multi-tenant tables** as defense-in-depth behind the application `WHERE tenant_id` filter — tenant isolation is non-negotiable (invariant #1), and the filtered-HNSW path (`02` §2.1b) is exactly where one missing predicate leaks. `db lint` **fails** if any `tenant_id` table lacks an RLS policy under a hosted-mode config; RLS is relaxed only for a provably single-tenant deployment. The cited anti-pattern (Syndai tables with RLS disabled, relying on grants) is what this avoids.

If a BYOC provider exposes the `memphant` schema to browser/API roles, then:

- RLS is enabled on exposed tenant tables
- policies are tested for tenant/scope isolation
- default privileges are pinned
- service/admin keys remain server-only
- generated SDKs never include service-role credentials
- direct table access is documented as unsupported unless explicitly enabled

Required checks:

```text
no memphant objects in public or syndai
no browser role can select memory rows without RLS policy
no function has mutable search_path
no table with tenant_id lacks tenant isolation index
no FK lacks an index
no extension placement drifts from provider strategy
```

Live Supabase inspection of the Syndai project surfaced many `syndai` tables with RLS disabled while Syndai relies on grant/exposure guardrails. MemPhant should avoid that ambiguity by making exposure posture explicit and tested from day one.

### 6.1.1 Tenant Isolation: The Layered Argument

**RLS alone is not sufficient** — 2026 saw concrete bypasses (CVE-2024-10976: policies disregarded under subqueries; CVE-2025-8713: optimizer statistics leaking rows RLS should hide). Invariant #1 is non-negotiable, so isolation is layered and **a leak is caught by a test, not a customer**:

1. **Application `WHERE tenant_id`**, tenant-prefixed index leading (`02` §2.1) — first line.
2. **RLS as defense-in-depth** — catches the one missing predicate, especially on the filtered-HNSW path (`02` §2.1b) where a small tenant in a shared index is where a dropped filter leaks; `db lint` fails without it.
3. **Physical partial-index isolation for large tenants** — a per-tenant `… WHERE tenant_id=:t` HNSW index *cannot* return another tenant's rows by construction; isolation and the recall-quality fix are the same lever.
4. **Connection hygiene** — pooled connections run `DISCARD ALL` on return so session GUCs/temp state can't bleed across tenants.
5. **Application-layer envelope encryption (3-tier DEK/KEK; default-on for sensitive `body`).** Sensitive plaintext — `episode.body`, `memory_unit.body`, and raw/cold blobs — is encrypted with a **per-user DEK** (AES-256-GCM), wrapped by a **per-tenant KEK**, rooted in a KMS/TEE **root KEK that never leaves KMS in plaintext** (AWS/GCP KMS envelope pattern). Postgres stores only **wrapped DEKs + KMS key refs** in a `key_custody` table — **never plaintext key material**; the plaintext DEK lives transiently in-process and is zeroized. Indexed `halfvec` vectors are **NOT** encrypted (an HNSW index must read plaintext to compute distance — `02` §2.1a; arXiv:2508.10373). **BYOC:** the customer holds their own KEK in their KMS and gets a hard kill-switch (revoke KEK → whole-tenant crypto-shred); MemPhant never holds their root material.

**The canary-tenant eval** (`05` §10) is not a one-time audit: it seeds tenant A, then issues *adversarial* recalls **as tenant B** across every channel (exact, lexical, vector-nearest to A's content, edge expansion) and asserts zero A-rows + `cross_tenant_recall_count = 0`, every PR, **with the small-tenant-in-large-corpus dimension** (`02` §2.1b) — single-tenant golden corpora never catch this.

## 7. Abuse Cases To Test

- Cross-tenant recall.
- Child agent recalls parent-only memory.
- Web page tells the agent to store malicious instruction.
- Tool output injects future tool parameters.
- Imported memory claims false user preference.
- Stale price/availability outranks current evidence.
- Deleted memory remains in vector results.
- Citation references an unknown memory ID.

### 7.1 Worked Attack: MINJA Against MemPhant

MINJA (arxiv 2503.03704) is the strongest published *query-only* memory-injection attack: an ordinary user with **no store access** plants a record through normal queries that later poisons a *different* victim's recall — the concrete instance of ASI06's "indirect injection → stored as trusted memory." Traced control-by-control:

| MINJA step | Attacker action | MemPhant control | Result |
|---|---|---|---|
| 1. inject via query | queries that make the agent emit + store malicious reasoning | **provenance (§3.2#1):** stored `source_kind=agent` → `agent_output` (0.4) | enters as `belief`, not high-trust |
| 2. progressive shortening | strips the indicator so the final record is clean | **content scan misses it** (§3.2 limit) — no imperative syntax | tripwire silent; defense must hold downstream |
| 3. victim retrieval | victim's query embeds-near the planted record | **read-time down-weight + high-risk suppression (§4/§4.2)** | survives into *evidence*, not *action* |
| 4. promote to "fact" | repeats to farm corroboration | **independent-source gate (`04` §5):** repeats share one `(actor_id, source_kind)` = one source | never clears belief→semantic |

**Residual risk (named, not hidden):** the payload still reaches the context window as a *labeled low-trust belief* few-shot example, and few-shot examples can bias generation even when labeled. MemPhant's honest boundary: it guarantees the record never becomes a citable fact, never fills a high-risk arg, never authorizes an action — it does **not** guarantee the model ignores a labeled low-trust example in free-text. That residual is the §4.2 suppression-label's job (the runtime must honor it) and a tracked eval (`reasoning-disguised injection` measures leakage into free-text answers as a *reported number*, not a pass/fail it can't honestly claim).

## 8. Public Security Posture

The Apache-2.0 core should include:

- threat model
- security policy
- responsible disclosure
- poisoning red-team fixtures
- tenant isolation tests
- provenance tests

Do not hide poisoning defense in the hosted tier. Security is the product.

## 9. Threat-to-Control Matrix

| Threat | Control | Eval |
|---|---|---|
| Persistent prompt injection | classify as low-trust/provisional, label in context | low-trust web poisoning |
| False semantic promotion | evidence/corroboration threshold | false fact suppression |
| Corroboration farming (Sybil) | corroboration requires **independent** sources (distinct actor_id AND source_kind), not count | corroboration-farming / Sybil poisoning |
| Resource-pointer SSRF | resolve-and-reject private/loopback/metadata; reject IPv4-mapped-IPv6 (§3.1) | resource-fetch SSRF lane |
| Procedural poisoning (poisoned "successful" strategy) | procedure promotion requires *adversarial* replay (not just success); safety-rerank suppresses high-risk steps regardless of similarity (`04` §4.2) | procedure-poisoning suite |
| Reasoning-disguised injection (MINJA) | provenance trust class scan can't override; independence gate; suppression label (§7.1) | reasoning-disguised injection (leakage reported, not pass/fail) |
| Intent-legitimation (benign memory legitimizes a harmful query) | the relevance gate (`05` §1.5) drops off-query memory before injection; memory is evidence not control (inv #4); high-risk-arg suppression (§4.2) | PS-Bench (`12`) — personalization can raise attack success 15.8–243.7% (arXiv:2601.17887); track, don't claim immunity |
| Stale world state | bitemporal validity, recency decay | stale fact override |
| Cross-tenant leak | tenant-prefixed queries, roles/RLS/grants | cross-tenant leakage |
| Child-agent overread | scope/agent policy at recall | child-agent isolation |
| Citation forgery | candidate whitelist and trace refs | citation forgery |
| Deleted vector hit | deletion generation and reindex checks | deletion completeness |
| Tool-arg bias | high-risk action suppression | high-risk tool arg suite |
| Telemetry leak | redaction and quality-fact separation | log/content redaction test |
| Filter/selector injection (cross-tenant read/poisoning bypass) | parameterized SQL only — recall filters/selectors are never string-interpolated into queries (the class behind Mem0 #5977/#5976 filter-expression injection, plus six store-specific injection fixes merged in one week; filters carry `user_id`/`agent_id`, so one unescaped value is a tenant bypass) | filter/selector injection suite |
| Degenerate-embedding poisoning (NaN/Inf/wrong-dim vectors) | validate embedding vectors at the write boundary — finite values, exact profile dimension — before any index insert (Graphiti #1505: degenerate vectors silently break dedup and propagate) | degenerate-embedding write rejection |
| User-scoped table escapes erasure | erasure manifest: every user-scoped table enrolled-or-exempt-with-reason, contract-tested; the deletion-generation registry survives the purge (§6.2) | erasure-manifest contract test |

## 10. Governed Actions Boundary

MemPhant may provide:

- relevant evidence
- confidence/trust labels
- citation paths
- stale/contradiction warnings
- suppression labels for low-trust memory
- **the typed contradiction/causal edges themselves**, so the calling agent can do ActMem-style implicit-conflict reasoning (arXiv:2603.00026 — e.g. "user asked where to buy a plant; memory says the user has a puppy; the plant is toxic to dogs → warn"). The substrate *exposes the signals*; it does not do the reasoning. This is the right side of "evidence not control": surfacing a `contradicts`/`depends_on` edge is evidence; deciding to act on it is the runtime's.

MemPhant must not provide:

- final approval for money, trading, procurement, hiring, or similarly consequential actions
- hidden policy overrides
- unreviewed tool parameters derived from low-trust memory
- workflow execution attempts

Syndai's governed-action runtime remains the owner of approval/execution. MemPhant is evidence, not control flow.
