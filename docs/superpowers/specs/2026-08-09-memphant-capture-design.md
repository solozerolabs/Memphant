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
| Extraction | **Deterministic, client-side, precision-first** | Sidesteps the flag-off server miner; keeps logic in one place; $0/offline. |
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
- **Secret mask** (`crates/memphant-core` or a small `memphant-redact` module). Rust, no network, **over-mask bias** (a missed secret is an incident; an over-mask is free). Regex-first on structured secrets (gitleaks MIT ruleset as source), entropy as a high-threshold gated secondary. Options: vendor the pattern set, or depend on `kingfisher` (MongoDB, Apache-2.0, pure-Rust, 1089 rules incl. Anthropic/OpenAI/Voyage). **Parity test against `scripts/github_lane_secrets.py`** so the Rust and Python packs cannot drift. Masking is irreversible (typed placeholders), applied to **tool-output turns too**, not just user turns.
- **Nominators (two, precision-first, deterministic).** Reuse core helpers `split_sentences`, `derive_fact_key`, `clean_object`, and **`contains_composition_risk` verbatim** (rejects "ignore policy"/"force push"/"rm -rf"). Do *not* bend the existing personal-preference `PREF_VERBS` miner — wrong grammar.
  - *Corrections nominator*: prohibition/imperative patterns ("don't", "never", "stop", "use X not Y", "revert") **plus the highest-yield structural signal — an instruction re-issued after the agent did something else** (n-gram recurrence across turns). Emits `kind:preference` candidates.
  - *Learnings nominator*: durable cross-repo/toolchain facts only (grep can't reach a sibling repo). Emits `semantic` candidates. Repo-recoverable facts are deliberately **not** minted.
- **Writer.** Posts through **existing verbs**: learnings/corrections as episodes or direct units via `retain` (`RetainPayload::Unit`), corrections as `kind:preference` scoped to the repo, harness runs to `/v1/task-outcomes`. Binds `repo_slug → scope.external_ref = repo:{slug}` via `PUT /v1/context-bindings/capture:{slug}` (idempotent under advisory lock), caching the five resolved IDs.
- **Outcome-gated promoter.** A reflect-time join: a `state='captured'` unit becomes `candidate`→`active` only when `task_memory_event` records a distinct later same-scope task `accepted_without_violation` with the unit in context (`attribution='deterministic_scorer'`). Until then it is inert — **not projected into MEMORY.md, not injected**.

### 4.2 Idempotency (three existing layers, all used)

1. **Primary — deterministic `Idempotency-Key` = `{source}:{file}:{offset}:{sha256}`** → mutation-ledger replay (never a duplicate write on re-run/`--watch` rescan). Mirrors `file_plane`'s `file-sync:{plan_sha256}:{uuid}`.
2. **Backstop — episode `dedup_key`** (content SHA, `ON CONFLICT … DO UPDATE` bumping `observation_count`): identical transcript content collapses to one unit regardless of cursor drift.
3. **Ledger uniqueness** — `unique(tenant, task_id)` + `transcript_sha256` for harness runs.

A lost or double-advanced cursor therefore **cannot** create duplicates — so the cursor is a local file, not a DB table.

### 4.3 Trust / tenancy

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
- **Corrections-precision gate** = an `evidence_contract`-carrying, **registered** artifact: labeled turn corpus (`.jsonl` + `.lock.json` sha, `gate_mine_goldens.py` discipline), precision floor with a Wilson lower bound, McNemar power via `instrument_power.py`. Non-vacuity by **perturbation** (remove the outcome edge → the promotion must flip), per the golden-nonvacuity rule.
- **Full gate** before "done": `check_spec_drift` (spec mirrored to Syndai), `instrument_power --check`, `check_evidence_contract`, `cargo fmt/clippy/test --workspace/--doc`, the scratch-DB `--ignored` tier.

## 8. Staged delivery

**Stage 0 — harness → ledger wire (days).** `memphant capture outcome`: Syndai/Pi/OpenCode harnesses POST run outcome + exposure to the existing `/v1/task-outcomes`. No parser, no daemon, no masking. The one indisputably-live consumer; ~90% present on this branch already.

**Stage 1 — Claude Code capture as an outcome-gated nominator (~1–2 wks).** The engine (§4): CC adapter, cursor+resume, Rust secret-mask, two deterministic nominators, writes as `state='captured'` (inert), outcome-gated promotion via the reflect-join. Ships with the **$0 offline validation** (§10). Optional Stop-hook + `--watch`.

**Stage 2 — GATED on Phase A ≥ 0.40 on ≥1 non-Sid cohort (plan-of-record G2).** Codex/Pi/OpenCode adapters; corrections → **active** per-repo rules projected into the session-start injection; the review/approve dashboard. Build only if the depth signal proves positive. If Phase A is flat, the plan-of-record's own conclusion fires — adherence is "a Syndai plumbing fix, not a product" — and Stage 2 is deleted, not shrunk.

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

## 12. Long-term-practice call (latency / performance / cost / UX)

- **Latency:** one-shot on session end via hook = capture is invisible to the user (runs after the turn). `--watch` uses `interval`+`select!` semantics (idle-cheap). SQLite (OpenCode) reads on `spawn_blocking`, read-only + WAL snapshot + `busy_timeout` so a live writer never blocks us.
- **Performance:** byte-cursor incremental reads (8 MiB/pass bound, oversized-line skip) — Codex's multi-GB sessions never load whole.
- **Cost:** **$0** — deterministic, offline, no model calls; reuses the reflect worker already running. The only paid path (server-side LLM reflect) stays optional and gated.
- **UX (above all):** zero manual `retain`; zero per-agent plugin required (tailer works retroactively and across harnesses — the differentiation vs the whole hook-based market); nothing wrong ever silently becomes an active rule (outcome-gated + nominate-not-activate); secrets never leave the machine unmasked. The user's win is *"my corrections and cross-repo lessons just show up, proven by outcomes, and nothing I didn't mean to share is stored."*
```
