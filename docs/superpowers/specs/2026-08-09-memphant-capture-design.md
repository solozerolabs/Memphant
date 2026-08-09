# `memphant capture` — design

**Date:** 2026-08-09
**Status:** design, pending owner review
**Author synthesis:** nine parallel analysis teams (codebase, tests, context7 docs, 2026 web research on 6 memory products, GitHub OSS survey, Supabase/database, web+mobile scoping, experimental, devil's advocate).

---

## 1. One-paragraph thesis

`memphant capture` ingests learning signal into MemPhant from coding-agent sessions **without any manual `retain` call**, and — this is the load-bearing decision — it does not trust its own extraction. A deterministic, precision-first extractor only **nominates** candidate learnings/corrections; the **outcome-coupled ledger we just shipped labels them**. A candidate is promoted to an active memory only when a *distinct later task in the same repo scope* records `accepted_without_violation` with that candidate in context. Capture is therefore not an instrument for the (measured-negative) adherence-enforcement thesis; it is the **causal-evidence generator for the outcome ledger**, which is the one live consumer. It ships with a $0 offline experiment that can kill it honestly.

## 2. Why this shape (the evidence that forced it)

Three findings from the research reshaped the naive "tail four transcripts, extract, write rules" baseline:

1. **Blind extraction is doomed on precision.** Deterministic correction lexicons hit ~100% precision at **~1% recall** (COLING'25 frustration paper); LLM judges reach only **~57% conversation-level accuracy** (CompliBench 2026). Our own adherence veto died at **0.317** learned-rule precision. A detector feeding *active* rules cannot clear a usable bar. → The extractor must **nominate, not decide**. The outcome ledger decides. This is already written into `docs/flows/outcome-coupled-evolution.md:7`: *"Regex can nominate candidates but cannot label them,"* and the mechanism (A4 = deterministic trigger **AND** positive outcome evidence) already scored **4/4 vs 2/4** in that flow's isolated replay.

2. **The market does not tail transcripts, but the tailer is still our seam.** Six 2026 memory products (Mem0, Zep, Letta, Cognee, Supermemory, Basic Memory) capture via explicit `add()`, lifecycle **hooks**, an in-server agent, or a proxy — *none* tails files. But every hook-based approach requires a per-agent install and cannot capture retroactive/offline history. The OSS survey confirms the tailer model (rogrep, ccusage, engineering-notebook) is the minority *and* the differentiated one: zero agent-side install, provider-agnostic, backfills history. **Resolution: the tailer is the engine; a hook is only an optional trigger over it.** We get the market's freshness and the tailer's portability.

3. **The consumers of two of three outputs are dead or grep-losing.** (Devil's advocate.) Injection is **+0.9pp FLAT** (OctoBench), the veto is **DEAD** (0.317, base-rate self-terminating), and learnings→recall loses to grep **96.67 vs 58.89** on repo-recoverable facts. → We do **not** build the active-rules / injection path now. Capture writes **passive `UnitState::Candidate` units** (`state='captured'` was deleted from the schema 2026-08-01 — `types/src/lib.rs:1087-1103`; Candidate inertness is a property of kind + low trust, landed automatically by `low_trust_projection_state`, `core/src/lib.rs:12512`); the *active* per-repo-rule path is gated behind Phase A (§9). The only learnings we mint are the non-vacuous slice grep can't reach: **cross-repo / toolchain** facts.

## 3. Scope decisions (authoritative)

| Question | Call | Rationale |
|---|---|---|
| Build the full feature now? | **No — staged, outcome-gated** (§8) | Devil's advocate: don't build active-rules machinery for dead consumers. Experimental: outcome-gating makes the ledger the consumer. |
| Which sources first? | **Claude Code JSONL only** in Stage 1 | Only Pi has a documented schema; CC is Sid's primary; the other three add parser tax + (OpenCode) a new dep. |
| Codex / Pi / OpenCode | **Deferred adapters** (Stage 2) | Same `SessionSource` trait; OpenCode needs `rusqlite` (the one net-new dep). |
| Mobile | **Cut** | No tailable mobile coding-agent transcript exists. |
| Web dashboard | **Deferred; the review surface already exists** | The corrections review queue is `GET /v1/scopes/{id}/projection` + the file-plane's MEMORY.md compile — a CLI surface today, no web build needed for Stage 1/2. |
| Residence | **CLI, one-shot default + optional `--watch` sleep-poll** | No server daemon (khive's mistake); no `notify` dep (matches the codebase's dependency-averse ethos). |
| Extraction | **Deterministic, precision-first; nominators in `memphant-core`, run SERVER-SIDE in reflect** (review-2 Finding 1) | Accuracy-first: more accurate than an LLM judge here, with the outcome ledger supplying recall. Server-side = one impl for all three transports (CLI + 2 Syndai); the CLI just masks + posts. Nominators stay **hand-rolled** (core refuses `regex` — `service.rs:6691`); only the edge mask uses regex, in the CLI. |
| New schema / route | **Zero for episodes/chat/outcomes.** The docs (resource) lane is the one place this breaks — deferred to Stage 1c | Database + codebase teams: every episode/unit/ledger field has a home. Resources lack dedup/valid-time/supersession (§14.3) — that lane defers rather than silently shipping broken staleness. |
| Agentic chats (Syndai) | **Same substrate, hook-push transport** (§14.2) | No tailable artifact exists; Syndai's `runner_post_turn` boundary hook is watermarked + idempotent already. Tri-domain proof: C1 chat lane already ingests via the identical `retain` verb. |
| RAG docs (Context7 etc.) | **Ingest-once per repo, Stage 1c, census-gated** (§14.3) | Version-pinned, immutable, currently refetched every call; but the resource lane needs a dedup/supersession write-path fix first, and the C2 docs lane previously failed its kill-gate — earn it with a census. |

## 4. Architecture

`memphant capture` is a new CLI module (`crates/memphant-cli/src/capture.rs`), dispatched from the existing hand-rolled `main.rs` arm (`main.rs:35`), sitting beside `http_verbs`/`file_plane` as a *transcript-parse + HTTP-client* layer. **No server route, no migration.**

**Mask at the edge, nominate at the center** (review-2 Finding 1). The client is thin — tail/hook + mask + post masked episodes. Nomination and promotion are **server-side** (the worker compile already hosts `extract_fact_candidates`, `service.rs:5923`), so all three transports share one nominator impl and Syndai (Python) never reimplements it. Only masking is per-transport, because secrets must die before they reach MemPhant.

```
 EDGE (per-transport, masks locally)                    CENTER (server, one impl)
 ───────────────────────────────────                   ─────────────────────────
 local CLI:    tail  → mask(rust) → POST /v1/episodes ┐
 Syndai chat:  hook  → mask(py)   → POST /v1/episodes ┼──▶ reflect compile:
 Syndai run:   seam  → mask(py)   → POST /v1/episodes ┘      episodic unit (always)
                        ▲                                  + NOMINATE → candidate
              rust↔py parity test                            preference/semantic (inert)
              is the DRY guard                                        │
                                                                      ▼
                                    outcome-gated promotion (STAGE 2, outcome-write path):
                                    candidate (inert) ──▶ active iff a distinct later
                                    same-scope task SERVED this unit (randomized_counterfactual
                                    lane) + recorded causal helpful credit. Stage 1: inert only.
```

3-layer idempotency (§4.3) still applies at every POST; cursor state stays a local file per transport.

### 4.1 Components (each independently testable)

- **`SessionSource` trait** → yields normalized `Turn { session_id, role, body, byte_span, ts }` plus a per-session **`SessionBinding`** (`subject_ref, actor_ref+kind, scope_ref+kind+parent_ref, agent_node_ref, access_policies`). `cwd`/`git_branch` live on the binding, **not** on `Turn` — that struct *is* the entire domain seam: coding → `scope_kind="repo"`, chat → `scope_kind="project"`, docs → the owning scope; nothing else in the pipeline branches on domain (§14.1). Stage-1 impl: `ClaudeCodeSource` (JSONL, `~/.claude/projects/<enc>/<uuid>.jsonl`). The normalized schema is **MemPhant's own**; OTel `gen_ai.*`/`session.*` names are a *one-way export mapping only* (OTel GenAI is Development-status, mid-rename, forked with OpenInference — do not adopt on disk).
- **Cursor + resume.** Byte-offset per `(file_id, path)` persisted to a git-ignored `MEMPHANT_CAPTURE_STATE_DIR` file (default `~/.memphant/capture/`). Rotation-safe via the `file-id` crate `(file_id, offset)` pair (atomic-replace/rotation detection). Read only complete lines; a partial trailing line is buffered, cursor advances to the byte after the last `\n`. **Contract (property-tested, borrowed from rogrep):** `parse(full) == parse(prefix) + resume(tail)`.
- **Secret mask** — lives in **`memphant-cli`** (correction to eng-review Finding 4: `regex` is *not* an existing dep anywhere in core/cli — it reaches the tree only via `tokenizers` in `memphant-runtime`, and core's fact miner explicitly refuses regex, `service.rs:6691`). So: **one new `regex` dep in the CLI only**, core stays regex-free, and the vendored **gitleaks MIT TOML ruleset** is the pattern source (still not `kingfisher`/Hyperscan — x86 SIMD fights "run anywhere"). Rust, no network, **over-mask bias** (a missed secret is an incident; an over-mask is free — the accuracy call for secrets: recall over precision). Entropy as a high-threshold gated secondary. **Parity test against `scripts/github_lane_secrets.py`** so the Rust and Python packs cannot drift. Masking is irreversible (typed placeholders), applied to **tool-output turns too**, not just user turns.
- **Nominators (two, precision-first, deterministic).** **Live in `memphant-core`, run server-side in the worker compile beside `extract_fact_candidates`** (review-2 Finding 1 — NOT in the CLI: three transports feed episodes and a Python reimplementation in Syndai would drift; server-side = one impl for all sources, and Syndai chats get nomination for free since they already POST `/v1/episodes`). Testable offline beside `fact_tests`. Reuse core helpers `split_sentences`, `derive_fact_key`, `clean_object`, and **`contains_composition_risk` verbatim** (rejects "ignore policy"/"force push"/"rm -rf"). Do *not* bend the existing personal-preference `PREF_VERBS` miner — wrong grammar. Gated by a capture-nomination flag on the reflect path (the `fact_extraction_enabled` seam), enabled for capture-sourced episodes.
  - *Corrections nominator*: prohibition/imperative patterns ("don't", "never", "stop", "use X not Y", "revert") **plus the highest-yield structural signal — an instruction re-issued after the agent did something else** (n-gram recurrence across turns). Emits `kind:preference` candidates.
  - *Learnings nominator*: durable cross-repo/toolchain facts only (grep can't reach a sibling repo). Emits `semantic` candidates. Repo-recoverable facts are deliberately **not** minted.
- **Writer.** Posts through **existing verbs**: learnings/corrections as episodes or direct units via `retain` (`RetainPayload::Unit`), corrections as `kind:preference` scoped to the repo, harness runs to `/v1/task-outcomes`. Episode bodies are the turn-aware join `"\n".join("{role}: {text}")` — the compiler's chunker windows on exactly that shape (`segment_episode_body`, `service.rs:6340`); role→`source_kind` uses the measured `EVENT_SOURCE_KINDS` map verbatim (`user→user`, `assistant→agent`, `toolResult→tool` — `scripts/code_lane_run_memphant.py:629`). Binding rules are **load-bearing** (§14.4): repo scope is created **with `parent_external_ref` = the user-root scope from day one** (parents are immutable after creation — `store.rs:5296`); agent_node refs are **scope-namespaced** (`claude-code@repo:{slug}` — `external_ref` is unique per subject and scope-pinned, so a bare `claude-code` hard-conflicts on the second repo, `store.rs:5353`); harness agent_nodes are **L0** (no parent — L1+ agents cannot own semantic/belief/preference, `types:301-307`, so a mis-leveled agent writes invisible units); **per-role actors** carry trust (trust is never caller-supplied — the server clamps from `actor.kind`: user→TrustedUser, tool→UnverifiedTool, `types:309-317`); and the binding carries `access_policies` (`Inherit{user-root, semantic}` for cross-repo toolchain facts; `Grant` for preference — Inherit cannot carry preference, `store.rs:1322-1333`). The CLI needs **new client code** for `PUT /v1/context-bindings` and `/v1/task-outcomes` (today's dispatch has neither), and binding requires a **tenant service key** — acceptable for dogfood; OSS installs get a documented choice (pre-provisioned binding vs scoped key) before Stage 2.
- **Outcome-gated promoter (Stage 2 — see eng-review Finding 1).** A `Candidate` unit becomes `active` only when the ledger records a distinct later same-scope task with the unit **exposed and** causally credited. **This requires serving the candidate**, because credit needs prior exposure: `helpful`/`harmful` events demand causal attribution (`explicit_user`/`deterministic_scorer`/`randomized_counterfactual`) and a `shown_unit_ids` exposure — an inert unit can never be shown, so it can never promote. (Nuance from the backend sweep: this is a **policy** deadlock, not a schema one — no DB constraint requires a prior `shown`; the decision stands because crediting an unshown unit is unsound. Also: `review_event`/`mark` is **not** an escape hatch — it is keyed to a `trace_id`, so it too requires prior exposure.) Live promotion therefore rides the **`randomized_counterfactual` serving lane** and moves to **Stage 2**, behind Phase A. The join fires on the **outcome-write path** (`record_task_outcome`/`record_task_memory_events` scanning for candidate units in that scope), not the capture-time reflect (eng-review Finding 2). **In Stage 1 candidates stay genuinely inert** — not projected, not injected, not served — and the gate is proven only offline (§10).

### 4.2 Ingestion correctness — substrates, backfill, and identity (owner re-review 2026-08-09)

This section resolves an ambiguity in earlier drafts: capture posts **the full masked session as episodes**, not just nominated candidates. The nominators are an *additional* precision path on top, not a filter in front. Substrate updates then flow through the machinery that already exists — capture adds transport, never a second compiler (DRY).

**Substrate-by-substrate (what updates, and by what machinery):**

| Substrate | Updated by | Capture's job |
|---|---|---|
| **Episodic** | Existing reflect compile: raw episode → episodic unit + contextual chunks + local embeddings (`service.rs:5901-5913`; C1 slice proven on real prod data) | POST each masked session turn-window as an episode via `retain` with correct `observed_at` |
| **Semantic / facts** | The two core nominators (cross-repo/toolchain learnings → `semantic`, `UnitState::Candidate`) — the server fact miner stays flag-off; repo-recoverable facts deliberately NOT minted (grep's turf) | Nominate, never decide |
| **Preference** | Corrections nominator → `kind='preference'`, `UnitState::Candidate`, repo scope | Nominate, never decide |
| **Bitemporal** | Existing valid-time/transaction-time machinery — stores never consult wall time (`lib.rs:1141`); supersession via the existing `correct` flow with `valid_from`/`valid_to` | Pass true event time (below); never fabricate validity |
| **Procedural / belief** | Existing reflect/write-router arms only, when the compiled unit warrants it | Nothing new — capture invents no extractor for these |
| **Resources (RAG docs)** | The existing `RetainPayload::Resource` path + resource chunking — but the lane has real write-path gaps (no dedup, no valid-time, no supersession — §14.3), so docs ingest is **Stage 1c, census-gated**, not silently included | Stage 1c only; when built, always set `kind:"document"` explicitly (the `Other` default silently disables chunking) |

**First run on an existing repo (backfill).** Default = **backfill from file start**, because retroactive capture is the differentiation (hooks can't do it). Rules:
- **Event time is the transcript's time, never ingest time**: `observed_at` = the JSONL line's `timestamp` (required field on `RetainEpisodeHttpRequest`). A two-month-old session must rank as two months old in the recency channel and sit correctly on the valid-time axis; stamping backfill as "now" would corrupt every temporal signal at once. Transaction time = ingest time, automatically — that's the bitemporal split doing its job.
- **Deterministic order**: files sorted (path, first-timestamp), lines in file order — re-running a backfill is byte-identical (the R0 re-ingest ordering lesson).
- **Bounded + resumable**: per-pass byte budget, cursors persist per file; a 4,105-transcript machine backfills across runs, not in one gulp. `MEMPHANT_CAPTURE_BACKFILL=0` opts a source into start-at-EOF (khive's toggle) for users who only want go-forward capture.

**Repo/user/harness identity (per-repo, per-user binding):**
- `subject` = the human user; `scope` = the repo (`external_ref = repo:{slug}`, **created with `parent_external_ref` = the user-root scope from day one** — parents are immutable after creation, so a parentless first bind forecloses cross-repo inheritance forever); `agent_node` = the harness, **scope-namespaced and L0** (`claude-code@repo:{slug}`, no parent) — the spine's three axes map exactly, no new concepts. Cross-repo toolchain facts live at the user-root scope and reach each repo via `Inherit{user-root, semantic}` + `Grant{preference}` policy rows minted at binding time (§14.4).
- **Worktree normalization**: slug derived from the transcript's `cwd`/project dir must collapse `<repo>--claude-worktrees-<name>` (and `.claude/worktrees/*` paths) to the canonical repo slug. This machine has **129** worktree/scratchpad transcript dirs today; without normalization each would mint a bogus scope and per-repo preferences would fragment.
- **Exclusions (fail-closed)**: transcripts under scratchpad/tmp paths (`/private/tmp/claude-*`, bench arms) are never ingested — eval-harness transcripts entering memory is contamination (dataset-integrity discipline). An unresolvable `cwd` skips the file with a counted warning, never a guessed scope.

**Ongoing use (live conversations).** Incremental tail per session file from the cursor; only complete lines. Claude Code's `parentUuid` forms a **DAG** — sidechain/subagent turns are parsed but marked (`is_sidechain`), and nominators run only on mainline user turns (a subagent's internal dialogue is not a user correction). The Stop-hook lag rule applies: trigger on `SessionEnd` or delayed, since the JSONL is written asynchronously. Every layer is idempotent (§4.3), so hook + cron + manual runs can overlap safely.

### 4.3 Idempotency (three existing layers, all used)

1. **Primary — deterministic `Idempotency-Key` = `{source}:{file}:{offset}:{sha256}`** → mutation-ledger replay (never a duplicate write on re-run/`--watch` rescan). Mirrors `file_plane`'s `file-sync:{plan_sha256}:{uuid}`.
2. **Backstop — episode `dedup_key`** (content SHA, `ON CONFLICT … DO UPDATE` bumping `observation_count`): identical transcript content collapses to one unit regardless of cursor drift.
3. **Ledger uniqueness** — `unique(tenant, task_id)` + `transcript_sha256` for harness runs.

A lost or double-advanced cursor therefore **cannot** create duplicates — so the cursor is a local file, not a DB table.

### 4.4 Trust / tenancy / binding auth

The served path runs as superuser → **RLS is bypassed**, so tenant/scope binding is enforced **in app code** via the existing `context_binding` handshake (never trust a client `tenant_id`). Capture inherits this pattern unchanged. Committed artifacts carry **hashes/counts/offsets only — never transcript bodies** (flow-doc rule). Subject-erasure cascades already cover the target tables.

**Binding auth (review-2 Finding 4, owner decision 2026-08-09 — pre-provisioned):** context-binding requires a tenant service key (`require_tenant_service_key`). That key **never ships to a capture install.** Instead: the user creates the binding once via an authenticated server-side flow (login/CLI-auth), and the capture CLI receives a **narrow, non-binding scoped key** that can only `retain` to its already-resolved context. No laptop holds a tenant-wide key. Dogfood (single trusted operator) may use the service key directly; the scoped-key flow is required before any public/OSS release.

**Binding self-verification (review-2 Finding 2 — fail loud, never silent):** the six binding rules in §14.4 all fail *silently* (invisible writes, foreclosed inheritance). After the handshake, capture MUST `verify_binding()` — assert the resolved agent is **L0**, assert the scope carries the expected **parent**, assert the **Inherit/Grant policy rows** applied — and **refuse to write** on any mismatch. A silent invisible-write on a memory product is worse than a crash. One guard, one test that a mis-leveled bind is rejected.

## 5. Triggers (over the one-shot engine)

- **Default:** `memphant capture` one-shot — tail all enabled sources from cursor, ingest, exit. Cron/manual/CI.
- **Freshness:** optional Claude Code **Stop/SessionEnd hook** that runs the one-shot. Honor the docs' async-write caveat — the JSONL lags, so use `SessionEnd` (or a short delay) rather than reading the transcript immediately on `Stop`.
- **Continuous:** `memphant capture --watch` = a sleep-poll loop around the same deterministic scan (no `notify` dependency). For long desktop sessions.

## 6. Extraction operating points (deliberately opposite)

| Extractor | Bias | Floor | Why |
|---|---|---|---|
| Corrections/learnings nominator | **precision** | Wilson lower-bound ≥ floor on a labeled golden corpus | Feeding memory; the veto's 0.317 is the cautionary number. |
| Secret mask | **recall (over-mask)** | every pattern in the parity corpus masked; raw value absent from all emitted fields | A missed secret is unrecoverable trust damage. |

## 7. Testing (in-repo idiom)

- **BDD sentence names**, doc-comment cites spec + the measured defect it regresses (house style).
- **Per-adapter** (mirror `fact_tests` table style): `parses_canonical_transcript_into_normalized_turns` (committed fixture under `tests/fixtures/capture/`), `rejects_unknown_shape_fails_closed`, `role_attribution_excludes_assistant_turns`, `byte_offsets_relocate_verbatim`.
- **Cursor idempotency** as a `memphant-store-testkit` scenario run on **both** stores (`pg_contract_test!`) — the store-divergence trap: the "have I seen this?" check must be an **unbounded key lookup**, never a bounded recall read. Plus the append test (grow by K bytes → ingest only the K-byte tail) and the rogrep `parse(full)==parse(prefix)+resume(tail)` property.
- **Secret-mask** parity test vs `github_lane_secrets.py`; `masked_output_never_contains_the_raw_secret`; the `postgres:postgres@localhost` CI-placeholder exclusion.
- **Ingestion correctness (§4.2):** `backfilled_episode_carries_transcript_time_not_ingest_time` (observed_at = line timestamp; recency channel ranks it old); `backfill_is_deterministic_across_reruns` (sorted files, byte-identical); `worktree_slug_normalizes_to_canonical_repo_scope`; `scratchpad_and_tmp_transcripts_are_never_ingested` (fail-closed, counted skip); `sidechain_turns_are_marked_and_excluded_from_nomination`.
- **Corrections-precision gate** = an `evidence_contract`-carrying, **registered** artifact: labeled turn corpus (`.jsonl` + `.lock.json` sha, `gate_mine_goldens.py` discipline), precision floor with a Wilson lower bound, McNemar power via `instrument_power.py`. Non-vacuity by **perturbation** (remove the outcome edge → the promotion must flip), per the golden-nonvacuity rule.
- **Full gate** before "done": `check_spec_drift` (spec mirrored to Syndai), `instrument_power --check`, `check_evidence_contract`, `cargo fmt/clippy/test --workspace/--doc`, the scratch-DB `--ignored` tier.

## 8. Staged delivery

**Stage 0-pre — apply migration 010 to the target DB (review-2 Finding 3).** `task_outcome`/`task_memory_event` exist only in the repo; Finn's `schema_migrations` tops out at `009`. Nothing in Stage 0/2 works until 010 is applied — and applying to the shared co-tenant Finn is its own careful step (scope to `memphant.*`, off-peak, verified). This is the true first task.

**Stage 0 — harness → ledger wire (days).** `memphant capture outcome`: Syndai/Pi/OpenCode harnesses POST run outcome + exposure to the existing `/v1/task-outcomes`. No parser, no daemon, no masking. The one indisputably-live consumer; ~90% present on this branch already.

**Stage 1a — corpus census + offline validation, one harness two modes (eng-review Finding 5 + review-2 Finding 6, $0, before any Rust).** Mode 1 (census): does the frozen Track-U / Claude corpus contain `correction → distinct-later-same-scope-task` chains at all? ~0 → outcome-gating is `UNTESTABLE`, stop here (cheapest kill). Mode 2 (validation): if chains exist, does outcome-gated minting beat blind minting on precision (§10)? Same script, two flags — not two builds.

**Stage 1b — Claude Code capture as an inert nominator (~1–2 wks, only if 1a passes).** The engine (§4): CC adapter, cursor+resume, gitleaks-rules secret-mask (in the CLI), two deterministic nominators **in core**, writes as `UnitState::Candidate` (**genuinely inert — not served**). Ships the **$0 offline validation** of the gate (§10) on the served-lesson corpus arms. Optional Stop-hook + `--watch`. **No live promotion here** (Finding 1 — that needs serving).

**Stage 1c — docs ingest-once (census-gated, and note this is a CORE change not CLI — review-2 Finding 5).** Context7/library docs → `RetainPayload::Resource` per repo, version-pinned (`uri` + `revision` both carry the version), recall-before-fetch in the consumer. **Blocked on** the resource-lane dedup/valid-time/supersession fix (§14.3), which is a **core reflect/store change** (the `ReflectResource` arm and `supersedes_own_kind`), heavier than the rest of capture — plus a usage census showing refetch volume worth saving. The C2 docs lane failed its kill-gate once already; this one earns its way in with numbers.

**Syndai chat + server-run lanes (Syndai-side work, parallel — §14.2).** Hook-push retains at the `runner_post_turn` boundary (chats) and at the `read_captured_stream` seam (server-run coding transcripts, whose sandboxes are deleted). Respects the Syndai coding-lane serving KILL (no memory injection before rung 10) — capture and outcome telemetry only.

**Stage 2 — GATED on Phase A ≥ 0.40 on ≥1 non-Sid cohort (plan-of-record G2). This is a major build, not an increment (review-2 Finding 5).** It requires *building the randomized-counterfactual serving lane* (a serving/injection experiment harness that does not exist today) so candidates can earn causal credit → **live** outcome-gated promotion on the outcome-write path; plus Codex/Pi/OpenCode adapters; corrections → **active** per-repo rules projected into session-start injection; the review/approve surface (already `GET /v1/scopes/{id}/projection`, not a new dashboard). Size it as a quarter, not a sprint. Build only if the depth signal proves positive. If Phase A is flat, the plan-of-record's own conclusion fires — adherence is "a Syndai plumbing fix, not a product" — and Stage 2 is deleted, not shrunk.

## 9. What we deliberately do NOT build (and when it unlocks)

- Active per-repo rule injection / veto → **Stage 2, gated on Phase A**. (Injection FLAT, veto DEAD today.)
- LLM extraction in the CLI → never in the hot path; server-side reflect (existing) may deepen for cloud tenants, optional.
- A `--watch` daemon as the *mechanism* → it's an optional trigger only.
- ChatGPT/Claude.ai web-export sources (khive had them) → irrelevant to coding memory.
- Any new table, route, or migration → none needed.

## 10. Cheapest validation ($0, offline, honest kill)

On the frozen Track-U / Claude transcript corpus: run the deterministic nominator to get the candidate set the baseline would blind-mint; join each candidate against the `task_outcome` ledger (or reconstruct outcomes from `compact_boundary` records as the flow doc already does). Report **precision of outcome-gated minting vs blind capture**, and the count of blind-minted units later followed by a *repeat* of the same violation (baseline's false positives). If outcome-gating lifts precision, build the join into `capture`. **If the corpus lacks enough correction→distinct-later-task chains, the scope is `UNTESTABLE`** — the same honest kill the flow doc uses — and we've spent nothing.

## 11. Open questions — answered

1. **Scope binding** → auto-derive `repo:{slug}` onto `scope.external_ref` via the context-binding handshake; zero config — **but not zero rules**: the repo scope must carry `parent_external_ref` = user-root at creation (immutable after), the agent ref is scope-namespaced + L0, and the binding mints the Inherit/Grant policy rows, or cross-repo facts and preference writes are silently invisible (§14.4).
2. **Residence** → CLI, one-shot default, optional Stop-hook trigger, optional `--watch` sleep-poll. Not a server daemon, no `notify` dep.
3. **Extraction** → deterministic, client-side, precision-first, promotion outcome-gated. No LLM in the CLI.
4. **Cursor storage** → local git-ignored file (`MEMPHANT_CAPTURE_STATE_DIR`); idempotency is already guaranteed three ways server-side.
5. **Identity / multi-machine / OSS** → per-machine cursors; tenant from the API key; subject = user; repo → scope; open-source users point `MEMPHANT_URL` at their own instance.
6. **Adapters** → `SessionSource` trait; ccusage per-provider layout; rogrep resume invariant; CC first, others deferred (OpenCode carries the one new dep, `rusqlite`).
7. **Web/mobile** → mobile cut; dashboard deferred (one screen if ever: corrections review/approve queue).

## 12. Long-term-practice call — priority is **accuracy > cost > speed**

The ordering is deliberate (owner directive 2026-08-09): when they conflict, accuracy wins over cost, cost over speed. Nothing below trades accuracy for a cheaper or faster path.

- **Accuracy (first):** the whole design is precision-first — the outcome ledger is ground truth, so a captured lesson only becomes active once outcomes confirm it; the offline gate proves this *before* the mechanism ships (§10). Secret masking is biased to over-mask (recall) because a missed secret is the costly error. Deterministic nomination is not chosen to be cheap — it's chosen because it's *more* accurate than an LLM judge here (COLING'25 ~1% recall but ~100% precision; CompliBench'26 judges ~57% conversation-level), and the outcome ledger supplies the recall the regex lacks. Server-side LLM reflect stays available as the accuracy-*deepening* path when a signal justifies it — cost never forecloses it.
- **Cost (second):** a happy consequence, not the driver — Stage 1 is $0/offline because deterministic-nominate + outcome-label happens to be both the accurate *and* the cheap path here. If accuracy ever demanded a paid pass, it would win.
- **Speed (last):** one-shot on session end (post-turn, invisible); `--watch` idle-cheap; byte-cursor bounds so Codex's multi-GB sessions never load whole; OpenCode SQLite on `spawn_blocking` + WAL snapshot + `busy_timeout`. Never at the expense of a dropped or mis-parsed turn — fail-closed over fast.
- **UX (the point):** zero manual `retain`; zero per-agent plugin required (the tailer works retroactively and across harnesses — the differentiation vs the whole hook-based market); nothing wrong ever silently becomes an active rule (outcome-gated + nominate-not-activate); secrets never leave the machine unmasked. The user's win is *"my corrections and cross-repo lessons just show up, proven by outcomes, and nothing I didn't mean to share is stored."*

---

## 13. Engineering review (2026-08-09, plan-eng-review)

Reviewed against DRY / well-tested / engineered-enough / explicit-over-clever / right-sized-diff, and the eng-manager patterns (blast radius, boring-by-default, reversibility, essential-vs-accidental complexity). Priority order **accuracy > cost > speed** (owner directive).

| # | Severity | Finding | Resolution (folded into this spec) |
|---|----------|---------|------------------------------------|
| 1 | **Load-bearing** | Outcome-gated promotion has a **serving deadlock**: the ledger grants `helpful`/`harmful` credit only with causal attribution + a `shown_unit_ids` exposure (`service.rs:5253`, migration `20260808_010`), but a `state='captured'` inert unit is never shown → can never promote. Live promotion is impossible in Stage 1 as written. | **Accepted (owner):** Stage 1 is offline-only (inert units, census-gated, $0). **Live promotion moves to Stage 2** on the `randomized_counterfactual` serving lane, behind Phase A. §4.1, §8. |
| 2 | Architecture | Promotion trigger mislocated — it fires when a *later* task's outcome lands, not at capture-time reflect. | Specified on the **outcome-write path** (`record_task_outcome`/`record_task_memory_events`), Stage 2. §4.1. |
| 3 | DRY | Nominators in the CLI would be a second extractor beside core's `extract_fact_candidates` → drift (store-divergence class). | **Nominators live in `memphant-core`**, CLI calls them. §3, §4.1. |
| 4 | Boring-by-default | `kingfisher`/Intel-Hyperscan masking dep is x86-centric SIMD — fights "run anywhere" + dependency-averse ethos, spends an innovation token needlessly. | **Vendor gitleaks MIT rules behind the `regex` crate** (best F1, portable, Layer 1). §4.1. |
| 5 | Tests / sequencing | The $0 offline validation is only meaningful if the corpus has `correction → later-same-scope-task` chains; the labeled coding-corrections corpus is the real critical-path cost. | **Corpus census is the Stage-1 entry gate** (Stage 1a), before any Rust — cheapest kill. Labeled-corpus construction called out as critical path. §8, §10. |

Reversibility: pre-production, every stage is independently deletable; Stage 2 deletes cleanly if Phase A is flat. Blast radius: only the Stage-2 promotion join touches the ledger write path; Stage 0/1 add no server surface. Two-week smell test: Stage 0 ships in days; Stage 1b is a bounded CLI + core-extractor change.

**VERDICT:** foundation sound (reuse, zero-schema, staged gating). One load-bearing fix (serving deadlock → offline-only Stage 1) accepted by owner; four tightening findings folded in. Proceed to implementation planning once the owner is satisfied with the revised staging.

**Decision resolved:** Stage-1 promotion deadlock → **offline-only Stage 1, serve in Stage 2** (owner, 2026-08-09).

NO UNRESOLVED DECISIONS

---

## 14. Tri-domain integration (owner review 2, 2026-08-09 — backend + database + Syndai teams)

The owner's requirement: MemPhant is tri-domain memory (agentic chats + RAG docs + code). Capture's infra must not couple to coding — coding repos themselves contain chats (user steering/asking about the codebase) and docs (cached library docs instead of re-fetching context7 per session). Three teams verified this against the code, the live Finn schema (read-only), and the Syndai worktree.

### 14.1 The decoupling verdict: 5 of 6 pipeline stages are already domain-agnostic

Cursor/resume, secret mask, `Turn` + the `role: content` body join, idempotency layers, and the verb POSTs are domain-free — **proof: the C1 Syndai chat lane and the code lane already ingest through the identical `retain` verb** (`docs/superpowers/specs/2026-07-21-c1-episodic-slice-design.md:64`, `scripts/code_lane_run_memphant.py:672`). The only coding-specific parts are *data and discovery* (transcript paths, cwd→slug normalization, DAG/sidechain handling, nominator lexicons). The seam is two small things, no plugin architecture:

1. **`SessionBinding` per session** (§4.1) — subject/actor/scope/agent refs + policies. Domain = which refs you bind, nothing more.
2. **A two-arm `Lane` on the writer**, mirroring `RetainPayload`'s existing arms: transcripts and chats → `Episodes`; doc corpora → `Resources`. Nominators run for every `Episodes` lane; a coding-flavored lexicon is data inside a nominator, not a structural fork.

`episode.source_kind` is **actor-typed, not domain-typed** (`user/agent/tool/web/resource/system` — closed CHECK), so chats fit with zero schema change; different source_kinds corroborating the same fact is already a reinforcement signal (free win). The task ledger stays correctly task-scoped: chats simply never POST outcomes (`validator_status='not_run'` is the designed escape hatch for validator-less tasks). Live DB check: **migration 010 is not yet applied to Finn** — its one file-flavored surface (`planned_files`/`actual_files`) is still cheap to rename before first apply if we ever care.

### 14.2 Chat lane (Syndai) — hook-push, and the hook already exists

There is **no tailable chat artifact**: Syndai turns live in `syndai.run_messages` server-side. The capture point is `runner_post_turn.py:66` — an existing per-boundary hook (every 10 turns + final) with deterministic boundary UUIDs (→ `source_ref`), a rendered `ROLE: content` transcript slice (→ episode body, already the chunker's shape), and DB watermarks (→ resume cursor). Zep-style push per boundary, not per turn. The C0 adapter (`memphant_dogfood_adapter.py`) has bind+recall wired and **payload builders for retain/correct/forget with zero callers** — the Syndai-side build gap is one episode-payload builder + one POST + a repo-chat binding family.

Scope binding per chat kind: plain chat → subject=user, scope=`syndai:agent:{id}` (today's shape); **chat about a repo** (`repo_answer`, which answers from a disposable read-only worktree) → subject=user, scope=`syndai:repo:{id}` with `parent_external_ref`=user-root — the adapter already supports `scope_parent_ref`; only a new `client_ref` family + widening the hardcoded cache key (`_context_cache_key`, adapter line 268). Capture must happen at the finalize seam — nothing in the disposable worktree survives.

**Server-run coding transcripts:** Syndai-executed CC sessions write JSONL **inside a remote sandbox that is deleted** — the local tailer has no purchase. The backend already holds the full stream at the `read_captured_stream` → cleanup seam (`executors/stream_capture.py:255`); one retain call there is the tail-equivalent. Same engine, two transports: local tailer for user machines, server seam for Syndai runs.

**Boundary honored:** Syndai's 2026-07-21 plan is an explicit, evidence-backed **KILL of MemPhant serving in the coding lane before rung 10** (`coding_claude_runner.py:133` swallows memory kwargs *by design*). Capture and outcome telemetry do not cross that line — nothing here injects memory into the coding lane; un-swallowing kwargs is Stage-2+, behind both Phase A and Syndai's own `memory_variant` A/B gate.

### 14.3 Docs lane (RAG) — the one place "zero schema/route" honestly breaks

Context7 docs are the right target: version-pinned ids, immutable per version, **currently refetched on every run and every mid-run docs call with no cache** (`docs_capability_handlers.py:54`), and `build_resource_retain_payload` sits unused in the adapter. But the resource write path has four real gaps the backend sweep proved:

1. **No dedup**: `stage_resource` is a plain INSERT — no `(uri, content_hash)` uniqueness; §4.3's idempotency layers do not cover resources.
2. **No valid-time**: the `ReflectResource` arm hardcodes `valid_from/valid_to = None` (`service.rs:5999`) — Rust-1.89 vs 1.96 docs have no validity axis.
3. **No supersession**: `supersedes_own_kind(Resource) → None` (`core/src/lib.rs:12591`) — a re-fetched doc appends, it can never close the stale generation.
4. **`extractor_state='stale'` is dead vocabulary** — legal in the CHECK, never written by any code path.

Fix is write-path code (same `uri` + new `content_hash` → mark predecessor stale + supersede derived units), not DDL — but it is real work, and the C2 docs slice already failed a free kill-gate once (11.5% vs a 60% bar). Hence **Stage 1c, census-gated**: measure the refetch volume first; build only if the numbers justify it. The Syndai knowledge base is **not** an ingest target (already embedded/versioned/hash-deduped; it is a later cut-over candidate per `07-syndai-integration-spec.md` §7).

### 14.4 Load-bearing binding rules (violating any of these silently loses data)

| Rule | Why | Cite |
|---|---|---|
| User-root scope bound **first**; every repo scope carries `parent_external_ref`=user-root **at creation** | Scope parents are immutable; a parentless first bind forecloses cross-repo inheritance forever | `store.rs:5296-5306` |
| Agent refs scope-namespaced: `claude-code@repo:{slug}` | `agent_node.external_ref` unique per subject + scope-pinned; bare `claude-code` hard-conflicts on repo #2 | `store.rs:5353-5375` |
| Harness/chat agent_nodes are **L0** (no parent) | L1+ agents cannot own semantic/belief/preference — writes land and are invisible (this exact failure already happened once) | `types:301-307`, `store.rs:5531-5536` |
| Cross-repo toolchain facts: mint at user-root, reach repos via `Inherit{user-root, semantic}`; preference needs `Grant` | ltree ancestry alone grants nothing; Inherit is L0 + episodic/semantic/belief + strict-ancestor only | `store.rs:1322-1333, 5538-5580` |
| Chat + coding agents in one repo scope: L0 **siblings** + reciprocal `Grant` rows | Same-scope siblings are not ancestors; visibility across agents is never automatic | `store.rs:5531-5545` |
| Per-role **actors** (user/tool), never caller-supplied trust | Server clamps trust from `actor.kind`; distinct actors give user turns TrustedUser and tool turns UnverifiedTool; note `dedup_key` includes actor, so cross-role identical bodies correctly don't collapse | `types:309-317`, `server/lib.rs:428` |

### 14.5 Review report addendum (second review)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 6 | **Must-fix (was wrong in spec)** | `state='captured'` does not exist — deleted 2026-08-01 | Spec now uses `UnitState::Candidate`; inertness = kind + low trust, automatic |
| 7 | **Must-fix (was wrong in spec)** | `regex` is not an existing dep; core explicitly refuses it | Mask moved to `memphant-cli` (one new dep there); core nominators stay hand-rolled |
| 8 | **Must-fix (was wrong in spec)** | `agent_node="claude-code"` hard-conflicts on the second repo | Scope-namespaced L0 agent refs |
| 9 | Load-bearing | Parentless repo scopes foreclose cross-repo inheritance forever | Bind user-root first; parent at creation (§14.4) |
| 10 | Load-bearing | Mis-leveled agents write invisible preference/semantic units | L0 rule + policy rows at binding (§14.4) |
| 11 | Architecture | Tri-domain decoupling | `SessionBinding` + `Lane` seam (§14.1); chats hook-push (§14.2); docs Stage 1c census-gated (§14.3) |
| 12 | Honesty | "Zero schema/route" overclaimed | Scoped to episodes/chat/outcomes; resource-lane gaps named (§14.3, §3) |

### 14.6 Review report addendum (review 2, 2026-08-09 — tri-domain + reliability)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | **Architecture/DRY (load-bearing)** | Client-side nomination forces Syndai (Python) to reimplement the Rust nominators across 3 transports | **Nomination moved server-side** (worker compile, `service.rs:5923`); mask stays per-transport at the edge (privacy), rust↔py parity test is the DRY guard. §4 diagram, §4.1 |
| 2 | **Must-fix (reliability)** | The six §14.4 binding rules fail SILENTLY (invisible writes, foreclosed inheritance) | `verify_binding()` after handshake — assert L0 + parent + policies, refuse to write on mismatch, fail loud. §4.4 |
| 3 | **Must-fix (sequencing)** | Stage 0 depends on migration 010, not applied to Finn (live tops at 009) | **Stage 0-pre**: apply 010 to the target DB first (careful on shared Finn). §8 |
| 4 | Security (owner fork → resolved) | Tenant service key on OSS installs = tenant-wide blast radius on every laptop | **Pre-provisioned binding + scoped non-binding key** (owner, 2026-08-09); service key never ships to an install. §4.4 |
| 5 | Blast-radius/honesty | Stage 2 (serving lane) and Stage 1c (resource write-path) read as increments but are major/core builds | Sized honestly: Stage 2 = a quarter (build the counterfactual serving harness); Stage 1c = a core reflect/store change. §8 |
| 6 | Minor DRY | Stage 1a census and §10 validation are two builds | One harness, two modes (census / validation). §8 |

**VERDICT (review 2):** foundation and tri-domain expansion sound. Finding 1 (server-side nomination) is the right structural call and makes every transport thinner. Findings 2-3 are must-fix and folded in; 4 resolved by owner; 5-6 folded. No new schema for episodes/chat/outcomes; the resource lane is honestly flagged as the exception.

**Decisions resolved (review 2):** nomination server-side (mask at edge); OSS binding = pre-provisioned + scoped key (owner, 2026-08-09).

NO UNRESOLVED DECISIONS
