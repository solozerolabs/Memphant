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

3. **The consumers of two of three outputs are dead or grep-losing.** (Devil's advocate.) Injection is **+0.9pp FLAT** (OctoBench), the veto is **DEAD** (0.317, base-rate self-terminating), and learnings→recall loses to grep **96.67 vs 58.89** on repo-recoverable facts. → We do **not** build the active-rules / injection path now. Capture writes **passive `state='captured'` units**; the *active* per-repo-rule path is gated behind Phase A (§9). The only learnings we mint are the non-vacuous slice grep can't reach: **cross-repo / toolchain** facts.

## 3. Scope decisions (authoritative)

| Question | Call | Rationale |
|---|---|---|
| Build the full feature now? | **No — staged, outcome-gated** (§8) | Devil's advocate: don't build active-rules machinery for dead consumers. Experimental: outcome-gating makes the ledger the consumer. |
| Which sources first? | **Claude Code JSONL only** in Stage 1 | Only Pi has a documented schema; CC is Sid's primary; the other three add parser tax + (OpenCode) a new dep. |
| Codex / Pi / OpenCode | **Deferred adapters** (Stage 2) | Same `SessionSource` trait; OpenCode needs `rusqlite` (the one net-new dep). |
| Mobile | **Cut** | No tailable mobile coding-agent transcript exists. |
| Web dashboard | **Deferred, optional** | MemPhant is a pure JSON backend; the only prior UI (WS-G) was a deleted fixture mock. If ever built, its one screen is a corrections review/approve queue. |
| Residence | **CLI, one-shot default + optional `--watch` sleep-poll** | No server daemon (khive's mistake); no `notify` dep (matches the codebase's dependency-averse ethos). |
| Extraction | **Deterministic, precision-first; nominators in `memphant-core`, called by the CLI** | Accuracy-first: more accurate than an LLM judge here, with the outcome ledger supplying recall. One implementation (Finding 3), not a CLI copy that drifts from the core miner. |
| New schema / route | **Zero** | Database + codebase teams: every field already has a home. |

## 4. Architecture

`memphant capture` is a new CLI module (`crates/memphant-cli/src/capture.rs`), dispatched from the existing hand-rolled `main.rs` arm (`main.rs:35`), sitting beside `http_verbs`/`file_plane` as a *transcript-parse + HTTP-client* layer. **No server route, no migration.**

```
transcript file ──▶ SessionSource ──▶ normalized Turn ──▶ secret-mask ──▶ nominator ──┐
 (CC JSONL)          (adapter)         (role,body,span)    (over-mask)     (precision)  │
                                                                                        ▼
                                        cursor state file  ◀── 3-layer idempotency  POST existing verbs
                                    (MEMPHANT_CAPTURE_STATE_DIR)                    /v1/episodes (retain)
                                                                                   /v1/… kind:preference
                                                                                   /v1/task-outcomes
                                                                                        │
                            ┌───────────────────────────────────────────────────────────┘
                            ▼
        outcome-gated promotion (reflect-join): captured ──▶ candidate ──▶ active
        iff a distinct later same-scope task recorded accepted_without_violation
        with this unit in context (task_memory_event, attribution=deterministic_scorer)
```

### 4.1 Components (each independently testable)

- **`SessionSource` trait** → yields normalized `Turn { session_id, role, body, byte_span, cwd, git_branch, ts }`. Stage-1 impl: `ClaudeCodeSource` (JSONL, `~/.claude/projects/<enc>/<uuid>.jsonl`). The normalized schema is **MemPhant's own**; OTel `gen_ai.*`/`session.*` names are a *one-way export mapping only* (OTel GenAI is Development-status, mid-rename, forked with OpenInference — do not adopt on disk).
- **Cursor + resume.** Byte-offset per `(file_id, path)` persisted to a git-ignored `MEMPHANT_CAPTURE_STATE_DIR` file (default `~/.memphant/capture/`). Rotation-safe via the `file-id` crate `(file_id, offset)` pair (atomic-replace/rotation detection). Read only complete lines; a partial trailing line is buffered, cursor advances to the byte after the last `\n`. **Contract (property-tested, borrowed from rogrep):** `parse(full) == parse(prefix) + resume(tail)`.
- **Secret mask** (`crates/memphant-core` or a small `memphant-redact` module). Rust, no network, **over-mask bias** (a missed secret is an incident; an over-mask is free — this is the accuracy call for secrets: recall over precision). **Vendor the gitleaks MIT TOML ruleset behind the `regex` crate we already compile** (eng-review Finding 4 — do *not* pull `kingfisher`/Intel-Hyperscan: an x86-centric SIMD dep fights "run anywhere" and the codebase's dependency-averse ethos; gitleaks has the best measured F1 anyway). Entropy as a high-threshold gated secondary. **Parity test against `scripts/github_lane_secrets.py`** so the Rust and Python packs cannot drift. Masking is irreversible (typed placeholders), applied to **tool-output turns too**, not just user turns.
- **Nominators (two, precision-first, deterministic).** **Live in `memphant-core` as pure functions; the CLI calls them** (eng-review Finding 3 — one extractor, not a CLI copy that drifts from the core `extract_fact_candidates`; testable offline beside `fact_tests`). Reuse core helpers `split_sentences`, `derive_fact_key`, `clean_object`, and **`contains_composition_risk` verbatim** (rejects "ignore policy"/"force push"/"rm -rf"). Do *not* bend the existing personal-preference `PREF_VERBS` miner — wrong grammar.
  - *Corrections nominator*: prohibition/imperative patterns ("don't", "never", "stop", "use X not Y", "revert") **plus the highest-yield structural signal — an instruction re-issued after the agent did something else** (n-gram recurrence across turns). Emits `kind:preference` candidates.
  - *Learnings nominator*: durable cross-repo/toolchain facts only (grep can't reach a sibling repo). Emits `semantic` candidates. Repo-recoverable facts are deliberately **not** minted.
- **Writer.** Posts through **existing verbs**: learnings/corrections as episodes or direct units via `retain` (`RetainPayload::Unit`), corrections as `kind:preference` scoped to the repo, harness runs to `/v1/task-outcomes`. Binds `repo_slug → scope.external_ref = repo:{slug}` via `PUT /v1/context-bindings/capture:{slug}` (idempotent under advisory lock), caching the five resolved IDs.
- **Outcome-gated promoter (Stage 2 — see eng-review Finding 1).** A `state='captured'` unit becomes `candidate`→`active` only when the ledger records a distinct later same-scope task with the unit **exposed and** causally credited. **This requires serving the candidate**, because credit needs prior exposure: `helpful`/`harmful` events demand causal attribution (`explicit_user`/`deterministic_scorer`/`randomized_counterfactual`) and a `shown_unit_ids` exposure — an inert unit can never be shown, so it can never promote. Therefore live promotion rides the **`randomized_counterfactual` serving lane** and moves to **Stage 2**, behind Phase A. The join fires on the **outcome-write path** (`record_task_outcome`/`record_task_memory_events` scanning for captured units in that scope), not the capture-time reflect (eng-review Finding 2). **In Stage 1 captured units stay genuinely inert** — not projected, not injected, not served — and the gate is proven only offline (§10).

### 4.2 Ingestion correctness — substrates, backfill, and identity (owner re-review 2026-08-09)

This section resolves an ambiguity in earlier drafts: capture posts **the full masked session as episodes**, not just nominated candidates. The nominators are an *additional* precision path on top, not a filter in front. Substrate updates then flow through the machinery that already exists — capture adds transport, never a second compiler (DRY).

**Substrate-by-substrate (what updates, and by what machinery):**

| Substrate | Updated by | Capture's job |
|---|---|---|
| **Episodic** | Existing reflect compile: raw episode → episodic unit + contextual chunks + local embeddings (`service.rs:5901-5913`; C1 slice proven on real prod data) | POST each masked session turn-window as an episode via `retain` with correct `observed_at` |
| **Semantic / facts** | The two core nominators (cross-repo/toolchain learnings → `semantic`, `state='captured'`) — the server fact miner stays flag-off; repo-recoverable facts deliberately NOT minted (grep's turf) | Nominate, never decide |
| **Preference** | Corrections nominator → `kind='preference'`, `state='captured'`, repo scope | Nominate, never decide |
| **Bitemporal** | Existing valid-time/transaction-time machinery — stores never consult wall time (`lib.rs:1141`); supersession via the existing `correct` flow with `valid_from`/`valid_to` | Pass true event time (below); never fabricate validity |
| **Procedural / belief** | Existing reflect/write-router arms only, when the compiled unit warrants it | Nothing new — capture invents no extractor for these |
| **Resources** | Out of capture's scope (file_plane/`retain` resource path already covers docs) | Nothing |

**First run on an existing repo (backfill).** Default = **backfill from file start**, because retroactive capture is the differentiation (hooks can't do it). Rules:
- **Event time is the transcript's time, never ingest time**: `observed_at` = the JSONL line's `timestamp` (required field on `RetainEpisodeHttpRequest`). A two-month-old session must rank as two months old in the recency channel and sit correctly on the valid-time axis; stamping backfill as "now" would corrupt every temporal signal at once. Transaction time = ingest time, automatically — that's the bitemporal split doing its job.
- **Deterministic order**: files sorted (path, first-timestamp), lines in file order — re-running a backfill is byte-identical (the R0 re-ingest ordering lesson).
- **Bounded + resumable**: per-pass byte budget, cursors persist per file; a 4,105-transcript machine backfills across runs, not in one gulp. `MEMPHANT_CAPTURE_BACKFILL=0` opts a source into start-at-EOF (khive's toggle) for users who only want go-forward capture.

**Repo/user/harness identity (per-repo, per-user binding):**
- `subject` = the human user; `scope` = the repo (`external_ref = repo:{slug}`); `agent_node` = the harness (`claude-code`, `codex`, `pi`, `opencode`) — the spine's three axes map exactly, no new concepts.
- **Worktree normalization**: slug derived from the transcript's `cwd`/project dir must collapse `<repo>--claude-worktrees-<name>` (and `.claude/worktrees/*` paths) to the canonical repo slug. This machine has **129** worktree/scratchpad transcript dirs today; without normalization each would mint a bogus scope and per-repo preferences would fragment.
- **Exclusions (fail-closed)**: transcripts under scratchpad/tmp paths (`/private/tmp/claude-*`, bench arms) are never ingested — eval-harness transcripts entering memory is contamination (dataset-integrity discipline). An unresolvable `cwd` skips the file with a counted warning, never a guessed scope.

**Ongoing use (live conversations).** Incremental tail per session file from the cursor; only complete lines. Claude Code's `parentUuid` forms a **DAG** — sidechain/subagent turns are parsed but marked (`is_sidechain`), and nominators run only on mainline user turns (a subagent's internal dialogue is not a user correction). The Stop-hook lag rule applies: trigger on `SessionEnd` or delayed, since the JSONL is written asynchronously. Every layer is idempotent (§4.3), so hook + cron + manual runs can overlap safely.

### 4.3 Idempotency (three existing layers, all used)

1. **Primary — deterministic `Idempotency-Key` = `{source}:{file}:{offset}:{sha256}`** → mutation-ledger replay (never a duplicate write on re-run/`--watch` rescan). Mirrors `file_plane`'s `file-sync:{plan_sha256}:{uuid}`.
2. **Backstop — episode `dedup_key`** (content SHA, `ON CONFLICT … DO UPDATE` bumping `observation_count`): identical transcript content collapses to one unit regardless of cursor drift.
3. **Ledger uniqueness** — `unique(tenant, task_id)` + `transcript_sha256` for harness runs.

A lost or double-advanced cursor therefore **cannot** create duplicates — so the cursor is a local file, not a DB table.

### 4.4 Trust / tenancy

The served path runs as superuser → **RLS is bypassed**, so tenant/scope binding is enforced **in app code** via the existing `context_binding` handshake (never trust a client `tenant_id`). Capture inherits this pattern unchanged. Committed artifacts carry **hashes/counts/offsets only — never transcript bodies** (flow-doc rule). Subject-erasure cascades already cover the target tables.

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

**Stage 0 — harness → ledger wire (days).** `memphant capture outcome`: Syndai/Pi/OpenCode harnesses POST run outcome + exposure to the existing `/v1/task-outcomes`. No parser, no daemon, no masking. The one indisputably-live consumer; ~90% present on this branch already.

**Stage 1a — corpus-census entry gate ($0, before any Rust; eng-review Finding 5).** Census the frozen Track-U / Claude corpus for `correction → distinct-later-same-scope-task` chains. If ~0 exist, outcome-gating is `UNTESTABLE` and Stage 1 does not proceed — the cheapest possible kill, *before* the build, not after.

**Stage 1b — Claude Code capture as an inert nominator (~1–2 wks, only if 1a passes).** The engine (§4): CC adapter, cursor+resume, gitleaks-rules secret-mask, two deterministic nominators **in core**, writes as `state='captured'` (**genuinely inert — not served**). Ships the **$0 offline validation** of the gate (§10) on the served-lesson corpus arms. Optional Stop-hook + `--watch`. **No live promotion here** (Finding 1 — that needs serving).

**Stage 2 — GATED on Phase A ≥ 0.40 on ≥1 non-Sid cohort (plan-of-record G2).** The `randomized_counterfactual` serving lane (so captured candidates can earn causal credit → **live** outcome-gated promotion on the outcome-write path); Codex/Pi/OpenCode adapters; corrections → **active** per-repo rules projected into the session-start injection; the review/approve dashboard. Build only if the depth signal proves positive. If Phase A is flat, the plan-of-record's own conclusion fires — adherence is "a Syndai plumbing fix, not a product" — and Stage 2 is deleted, not shrunk.

## 9. What we deliberately do NOT build (and when it unlocks)

- Active per-repo rule injection / veto → **Stage 2, gated on Phase A**. (Injection FLAT, veto DEAD today.)
- LLM extraction in the CLI → never in the hot path; server-side reflect (existing) may deepen for cloud tenants, optional.
- A `--watch` daemon as the *mechanism* → it's an optional trigger only.
- ChatGPT/Claude.ai web-export sources (khive had them) → irrelevant to coding memory.
- Any new table, route, or migration → none needed.

## 10. Cheapest validation ($0, offline, honest kill)

On the frozen Track-U / Claude transcript corpus: run the deterministic nominator to get the candidate set the baseline would blind-mint; join each candidate against the `task_outcome` ledger (or reconstruct outcomes from `compact_boundary` records as the flow doc already does). Report **precision of outcome-gated minting vs blind capture**, and the count of blind-minted units later followed by a *repeat* of the same violation (baseline's false positives). If outcome-gating lifts precision, build the join into `capture`. **If the corpus lacks enough correction→distinct-later-task chains, the scope is `UNTESTABLE`** — the same honest kill the flow doc uses — and we've spent nothing.

## 11. Open questions — answered

1. **Scope binding** → auto-derive `repo:{slug}` onto `scope.external_ref` via the context-binding handshake; zero config. (Codebase team confirmed this is the exact supported mapping.)
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
```

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
