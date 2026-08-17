# Substrate completeness — hand-off

**Status:** analysis done, implementation NOT started. Two work items greenlit
(2026-08-16). No paid runs. This doc is the single source of truth for the next
session; the ready-to-paste kickoff prompt is at the bottom.

---

## 0. Why this exists (the one-paragraph why)

MemPhant's capture→recall→inject loop now closes end-to-end and honestly (Stage
A+B: captures land `Candidate`, are served labelled `[unconfirmed]`, and a
survival witness promotes them). A substrate audit (three parallel traces,
2026-08-16) then asked the harder question: of everything the schema supports —
vector embeddings, knowledge-graph edges, bitemporal time, FSRS decay, the six
memory kinds, compact lanes, reflect jobs — **how much actually reaches the
agent on the served path, and correctly?** The answer: the substrate is
**schema-rich but served-thin**. Most of it is genuinely wired. Two pieces are
not, and one of them (vector recall on the agent's own MCP tool) quietly
handicaps every downstream value measurement. This hand-off fixes both.

---

## 1. As-built substrate map (audit verdict, 2026-08-16)

| Substrate / type | Served-path verdict | Evidence |
|---|---|---|
| **Bitemporal** (valid/txn time) | ✅ fully wired | `valid_at`/`transaction_as_of` reachable on the wire (`memphant-types/src/lib.rs:2048-2049`); superseded rows excluded by the txn boundary (`memphant-core/src/lib.rs:10897-10917`); corrections append successors (`lib.rs:1540-1567`, `13957-13973`) |
| **File projection** (MEMORY.md) | ✅ wired, two intentional products | Python read-only `.memphant/MEMORY.md`+AGENTS.md pointer (`plugins/_shared/memphant_projection.py`); Rust read-write canonical `MEMORY.md`+`units/` (`memphant-cli/src/file_plane.rs`, `memphant-mcp/src/file_memory.rs`). Boundary pinned in `docs/specs/canonical-file-projection.md`. No "rules"/`learnings.md` product exists — both emit a memory *index* |
| **Kinds** (6 variants) | ✅ wired; channel-provenance, no classifier | `capture_kind` deterministic match, no LLM (`memphant-core/src/service.rs:7914-7919`). Capture mints only Semantic (mirror/summary) + Procedural (errfix). **Belief & Preference no longer produced by the capture pipeline** — Belief = quarantine/fallback default only; Preference = explicit `retain` API only |
| **compact / union lane** (`serve_captures`) | ✅ wired | `compact_only` implies `serve_captures`; union coding lane serves captured Candidates, general lane hides them (anti-poison) — `memphant-core/src/lib.rs:10845-10892` |
| **Reflect / sleep jobs** | ✅ wired, real consolidation | `claim_reflect_jobs` (SKIP LOCKED) → `persist_compiled_units` writes merges/supersedes/derives (`lib.rs:12134-12145`); kind-dispatched supersession (`lib.rs:13144-13158`) |
| **reinforcement_count / confidence** | ✅ persisted-and-served | lifecycle UPDATE increments `reinforcement_count` (`store.rs:4952-4953`); `confidence` bound on insert, surfaced on `MemoryRecord` |
| **Vector embeddings** | ⚠️ **split-brain** | Write path embeds (`service.rs:4787,4900,5320`), store does exact `<=>` halfvec scan per-profile (`store.rs:2908,2960`), **HTTP `/v1/recall` serves vectors** (`memphant-server/src/lib.rs:532`). **But the native MCP `recall` tool is `NoopEmbedding` by design** (`memphant-mcp/src/lib.rs:479` → `service.rs:3678-3683`) → lexical-only. **← WORK ITEM A** |
| **FSRS decay** | ⚠️ **partial** | retrievability reorders **marked units only** (`lib.rs:11842-11870`, `7954`) via `review_event`+`reinforcement_count`. The `stability_days`/`difficulty` dead columns were **DELETED** (Decision C, 2026-08-17, migration `20260817_013`); the decay seed reads the `DEFAULT_*` constants. |
| **Knowledge-graph edges (recall expansion)** | ⚠️ **unwired + non-functional** | `edge_expansion_enabled` hardcoded `false` in the only public entry (`service.rs:4532`), absent from the wire type; forced-on it still surfaces nothing (see §3). **← WORK ITEM B** |
| **Knowledge-graph edges (lineage substrate)** | ✅ used — do NOT delete | `DerivedFrom`/`Supersedes` drive correction lineage, invalidation, bitemporal remainder splitting (`lib.rs:1519,1604-1608,1640-1642,5350`) |

**One-line answer to "is the substrate done?":** bitemporal, file projection,
kinds, compact, reflect, reinforcement — yes. Vector (MCP tool), FSRS
write-back, and edge recall-expansion — no. Items A and B below fix the first
and third; FSRS is parked with a clear future call.

---

## 2. WORK ITEM A — vector recall on the native MCP tool

> **✅ DONE 2026-08-17.** The recall clone now keeps the local embedder:
> `provider_free_recall_clone` → **`ambient_free_recall_clone`**
> (`memphant-core/src/service.rs`) strips only the ambient providers
> (cross-reranker, deep-recall), not the embedder. The downstream vector gate
> (`service.rs` `if self.embedder.dimensions() > 0`) already falls back to
> lexical for a no-model (`NoopEmbedding`) build, so the portable lane is
> preserved without the strip. Perturbation-checked test:
> `crates/memphant-core/tests/provider_free_recall.rs::recall_clone_serves_the_vector_channel_for_lexically_disjoint_queries`
> (lexically-disjoint, embedding-near query surfaces the unit via the Vector
> channel; remove the embedder → the channel goes dark). The historical
> as-was analysis below is retained for the record.

### Problem
The agent's own `recall` MCP tool is semantically blind. `MemphantMcp::new`
builds `recall_service = service.provider_free_recall_clone()`
(`memphant-mcp/src/lib.rs:479`), and that clone hard-sets
`embedder = Arc::new(NoopEmbedding)` (`memphant-core/src/service.rs:3678-3683`).
`NoopEmbedding.dimensions() == 0`, so in `recall_internal` the query embedding
is `None`, the Vector channel never runs (`service.rs:4582-4605`), and the tool
returns **lexical-only** results. The dense vector channel is served **only** on
the HTTP `/v1/recall` path (which the Python file-projection uses), never on the
native MCP tool.

The `fastembed` default feature I added to `memphant-mcp/Cargo.toml` is still
correct — but it serves the **write/`remember`** path (embeddings must be
computed on ingest so `/v1/recall` has vectors to search), NOT the MCP recall
tool. The Cargo comment was corrected on 2026-08-16 to say so.

### Why it matters
1. **Product truth:** "semantic recall" is the pitch; the agent's primary
   retrieval tool doesn't do it.
2. **It contaminates measurement:** the Stage-C efficiency study (does memory
   save re-derivation?) is unfair if the agent's recall is lexical-only. Fix A
   is a prerequisite for a clean Stage-C run.

### First step (verify before wiring)
Confirm which surface actually delivers to the coding agent in the dogfood loop:
- If delivery is the **file projection** (HTTP → vectors), the MCP tool being
  lexical is lower-stakes (but still wrong).
- If the **hooks inject via the MCP `recall` tool**, injected memory is
  semantically blind — high-stakes. Check the hook wiring
  (`plugins/`, the MCP inject path) to see which `recall` the agent receives.

### The fix (once the path is confirmed live)
Give the MCP recall path a real embedder instead of `NoopEmbedding`. Options,
cheapest first:
1. **Reuse the write-side embedder** the MCP already binds for `remember`
   (`self.service` at `memphant-mcp/src/lib.rs:591`) — the recall clone exists
   only to strip the provider; the embedder is already loaded in-process, so the
   clone can keep it. Verify why `provider_free_recall_clone` strips it (the
   "compact-only portable lane" rationale at `lib.rs:630-636`) and whether that
   rationale still holds now that fastembed ships by default.
2. If the portable-lane guarantee must stay (e.g. a no-model deployment), make
   it **conditional**: use the live embedder when `dimensions() > 0`, fall back
   to Noop otherwise — so a fastembed build gets vectors and a stripped build
   still runs lexical.

### Acceptance
- A `recall` through the MCP tool on a corpus where the answer is
  **lexically disjoint** from the query but **semantically close** returns the
  right unit (it does NOT today).
- New test in `memphant-mcp` (or `memphant-core` if the embedder seam is there):
  seed a unit whose body shares no query tokens but is embedding-near; assert
  the MCP recall path surfaces it. Perturbation-checked (remove the embedder →
  case flips), per the golden-non-vacuity rule.
- No regression on the lexical path; `cargo test --workspace` green.

---

## 3. WORK ITEM B — fix + measure edge recall-expansion

> **✅ DONE 2026-08-17 → verdict LEAVE-OFF (flag exposed, substrate stays).**
> The channel was **never broken**: the "non-functional when forced on" null was
> a test-harness artifact — `dormant_signal_value.rs::seed_edge` staged edges
> under `SystemClock` (future `transaction_from` vs the fixed recall clock), so
> `fetch_edges` correctly excluded them. Seeding at the recall clock makes edge
> ON pull in the query-disjoint `DerivedFrom` detail; the 12-topic corpus harness
> reports `details_on=12 details_off=0 poison_hits=0` (value-positive,
> non-poisoning). Done: repro fixed, EDGE-1/EDGE-2 asserts load-bearing +
> perturbation-checked, corpus measurement added. **Flag stays server-controlled**
> (core `RecallRequest`, hardcoded off) — NOT a public wire field: a tested
> invariant (`rest_contract.rs`) forbids engine-control flags
> (`edge_expansion_enabled`/`decay_enabled`/`rerank_enabled`) from the public
> request schema, so a served A/B uses a server-side toggle, not client input.
> **Default stays OFF:** the Edge channel only re-scores units ALREADY in the
> candidate pool — no dedicated edge-neighbor fetch — so on Postgres (FTS +
> recency-100 + fact-key pool) a query-disjoint, non-recent edge-reachable unit
> never enters the pool. The InMemory corpus number overstates reach (InMemory
> returns all units). Path to unlock: an edge-neighbor pool fetch, re-measured on
> a Postgres-backed corpus. Full record: 26 §10 Decision B. As-was analysis below.

### Problem (two layers)
1. **Unwired in production:** the only public entry hardcodes
   `edge_expansion_enabled: false` (`service.rs:4532`) and the flag is absent
   from `RecallHttpRequest` (`memphant-types/src/lib.rs:2026-2052`). No HTTP or
   MCP request can turn it on. The Edge channel is filtered out of the pass list
   whenever the flag is false (`lib.rs:7808`).
2. **Non-functional even when forced on:** `crates/memphant-core/tests/dormant_signal_value.rs`
   force-enables the flag through the core `recall` (bypassing the service
   wrapper). Re-run 2026-08-16:
   ```
   [EDGE-2 DerivedFrom pull-in] OFF=["Authentication uses OAuth2 with PKCE."]
                               ON =["Authentication uses OAuth2 with PKCE."]   ← detail NOT pulled in
   ```
   By the `edge_score` mechanism (`lib.rs:10788+`), `detail` **should** score
   1.0 when ON — it is `DerivedFrom`-linked to `head`, and `head` matches the
   query, so `related_match` should be true. It is served in **neither** lane.
   The candidate is dropped **downstream of channel scoring** (fusion/packing) —
   the exact drop line was **not pinned** in the audit. So the current null is a
   **vacuity/bug signature, not proof edges are useless.** We have never
   measured working edge expansion.

### Critical scope note
The edge **substrate** is load-bearing for corrections/lineage (`lib.rs:1519`,
`1604-1608`, `1640-1642`, `5350`) — **do not delete edges.** This item touches
only the **recall-expansion channel** (`ChannelPass::Edge`, `edge_score`, the
`edge_expansion_enabled` flag, and its wire exposure).

Also: only 3 of 6 edge kinds are ever written (`Supersedes`, `DerivedFrom`,
`SameSubject`); `Contradicts`, `Cites`, `DependsOn` are never constructed
(`memphant-types/src/lib.rs:1572-1580`). Expansion design should target the
written kinds.

### The fix
1. **Find and repair the downstream drop** so an edge-linked, non-lexically-
   matching unit actually reaches the served set. Start by instrumenting
   `dormant_signal_value.rs` EDGE-2: log the fused/packed set to see where
   `detail` disappears after `channel_candidates` returns it with score 1.0.
   Suspects: RRF fusion requiring presence in a "primary" channel, the
   admission/packing gate dropping edge-only candidates, or channel weight
   zeroing it.
2. **Expose the flag on the wire** (`RecallHttpRequest` + MCP recall args),
   default `false`, so the served path can turn it on per-request and the
   Stage-C harness can A/B it. Keep the service default off until measured.

### Measure (non-vacuous)
- Turn the two EDGE scenarios in `dormant_signal_value.rs` into **load-bearing
  asserts** (currently the edge verdicts are `eprintln!` only; only the
  non-edge invariant is asserted). Assert: with edge ON, `detail` is served;
  with edge OFF, it is not; **remove the edge → the ON case must flip** (the
  perturbation that proves non-vacuity).
- Then a corpus-level measurement on a coding haystack where related detail is
  edge-reachable but query-disjoint: does edge expansion raise retrieval of the
  load-bearing unit without poisoning (must NOT resurface superseded content —
  EDGE-1 stays neutral/safe). This is the value verdict.

### Acceptance
- EDGE-1/EDGE-2 asserts are load-bearing and pass, perturbation-verified.
- Edge expansion reachable via a request flag; default stays off until the
  corpus measurement returns a positive, non-poisoning verdict.
- Decision recorded in `docs/superpowers/specs/memphant/26-decision-register.md`:
  wire-on (value shown) or leave-off (value absent) — but NOT delete (substrate
  stays).

---

## 4. Already done
- **DECISION C — FSRS dead columns: DELETED (2026-08-17).** `stability_days`/
  `difficulty` were staged NULL and never written back (no `UPDATE ... SET`
  existed); the decay seed always resolved to the `DEFAULT_*` constants. Dropped
  both columns (migration `20260817_013`, `breaking`) + type fields + PG bindings
  + `DecayScore`/`RecallCandidateTrace.dsr_*`; OpenAPI + trace-schema snapshots
  regenerated. Chose delete over persist (YAGNI). See 26 §10 Decision C.
- **WORK ITEM B — edge recall-expansion: fixed, measured, LEAVE-OFF (2026-08-17).**
  Root-caused the "non-functional" null to a harness artifact (edges seeded with a
  future `transaction_from`); channel works. Load-bearing perturbation-checked
  EDGE-1/EDGE-2 asserts + a 12-topic corpus measurement
  (`details_on=12 details_off=0 poison_hits=0`). Flag stays a server-controlled
  engine-control field on the core `RecallRequest` (hardcoded off), NOT a public
  wire field — a tested invariant forbids it there. Verdict LEAVE-OFF: no
  edge-neighbor pool fetch exists, so production reach is pool-bounded and the
  InMemory number overstates it. Substrate untouched. See 26 §10 Decision B.
- **WORK ITEM A — vector recall on the native MCP tool (2026-08-17).** Renamed
  `provider_free_recall_clone` → `ambient_free_recall_clone` and stopped
  stripping the local embedder; the MCP `recall` tool now serves the dense
  vector channel. One-line fix (delete the `NoopEmbedding` swap) + honesty
  rename + perturbation-checked test; the old lexical-only-contract test flipped
  to assert the embedder is kept while ambient providers stay stripped.
- `capture_kind`: mirror/summary → **Semantic** (was Belief), errfix →
  Procedural (`service.rs:7914-7919`).
- Union recall lane (`serve_captures`) + dual file-projection pinned
  (`docs/specs/canonical-file-projection.md`, `cli-recall-default-lane.md`).
- `memphant-mcp/Cargo.toml` comment corrected (fastembed serves the write path,
  not the MCP recall tool).
- `service.rs:7945` doc corrected (Semantic, not Belief).
- `docs/superpowers/specs/memphant/05-retrieval-and-eval-spec.md` §1.1 carries an
  "As-built note (served-path audit)" recording the Stage-3 vector split-brain,
  the Stage-4 edge channel being unwired/non-functional (substrate stays), and
  FSRS retrievability reordering marked units only — pointing back here.

## 5. Future (beyond A + B)
- **Stage-C efficiency measurement** (gated paid run, n≥15–20 paired): does
  memory save re-derivation? Only fair once A lands. Design first ($0): source
  must be deleted/hidden before recall, else the agent re-greps and memory saves
  nothing. See `[[memphant-stageAB-and-surface-verdict]]`,
  `[[memphant-capture-revived-2026-08-15]]`.
- **FSRS write-back or delete:** ✅ **DONE 2026-08-17 → deleted** (Decision C, §4).
  The dead `stability_days`/`difficulty` columns were dropped. If a real
  spaced-repetition schedule is ever wanted, it is a fresh feature (persist
  computed state on review), not a resurrection of these columns.
- **Kind classifier vs channel-provenance:** kind is currently pure channel
  provenance. A classifier is a separate, unproven bet — not in scope here.

## 6. Verification harness (run before claiming done)
```sh
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
cargo test -p memphant-core --test dormant_signal_value -- --nocapture
```

---

## 7. Kickoff prompt for the new session

> You are picking up MemPhant substrate-completeness work. Read
> `docs/specs/substrate-completeness-handoff.md` first — it is the single source
> of truth (as-built map, file:line anchors, why, acceptance). Then execute BOTH
> work items; do not run any paid measurement.
>
> **Context:** MemPhant is a Rust workspace (`memphant-core/-mcp/-server/-worker/
> -cli/-types/-store-postgres`) + Postgres + Python capture plugins. It gives
> coding agents cross-session memory: capture → recall → inject. The
> capture→inject loop is closed and honest. A 2026-08-16 substrate audit found
> the served path is schema-rich but thin in two places.
>
> **Work item A — vector recall on the native MCP tool.** The MCP `recall` tool
> uses `provider_free_recall_clone()` → `NoopEmbedding`
> (`memphant-mcp/src/lib.rs:479`, `memphant-core/src/service.rs:3678-3683`), so
> it is lexical-only; vectors are served only on HTTP `/v1/recall`. First verify
> which surface actually delivers to the agent (file projection via HTTP, or
> hooks via the MCP tool). Then give the MCP recall path a live embedder
> (reuse the write-side one the binary already loads; make it conditional on
> `dimensions() > 0` if the portable no-model lane must survive). Acceptance: an
> MCP recall on a lexically-disjoint-but-semantically-near query returns the
> right unit; perturbation-checked test; no lexical regression.
>
> **Work item B — fix + measure edge recall-expansion.** The edge-expansion
> channel is unwired (`edge_expansion_enabled` hardcoded false, `service.rs:4532`,
> not on the wire type) AND non-functional when forced on: in
> `crates/memphant-core/tests/dormant_signal_value.rs` the `DerivedFrom` detail
> is not pulled in even though `edge_score` should return 1.0 — it is dropped
> downstream of channel scoring (fusion/packing), root cause UNPINNED. Do NOT
> delete edges — the substrate drives correction lineage
> (`lib.rs:1519,1604-1608,5350`). Fix only the recall-expansion channel: find
> the downstream drop, make edge expansion actually surface an edge-linked
> non-matching unit, expose the flag on the wire (default off), turn the EDGE-1/
> EDGE-2 verdicts into load-bearing perturbation asserts, then measure value on a
> coding haystack (must raise retrieval without resurfacing superseded content).
> Record the wire-on/leave-off decision in `26-decision-register.md`.
>
> **Constraints:** accuracy > cost > speed; KISS/DRY; amend canonical docs, don't
> spawn parallel plans; gate every paid call (there are none here); no
> back-compat needed (pre-production). Verify with the harness in §6 before
> claiming done. This repo commits to `main` directly.
>
> Start by reading the hand-off doc and confirming the live delivery path for A.
