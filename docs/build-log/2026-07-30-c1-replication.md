# C1 replication (W9) — bars re-proven, paired probe structurally impossible

Date: 2026-07-30 · Branch `af-w9-c1` · Owner-authorized production read.
Prereg: `docs/build-log/2026-07-30-c1-replication-privacy-prereg.md`.
Prior: `docs/build-log/2026-07-22-c1-episodic-slice.md`.

## Verdict

Three results, in decreasing order of how much they change the plan.

1. **Ownership condition (d) cannot be settled on C1, and no amount of care
   changes that.** The plan requires "a paired win replicated on the C1 slice".
   The C1 slice contains **44 recall-visible episodes across 5 tenants**, and
   retrieval is tenant-scoped, so four of the five tenants have pools of 9, 6, 4
   and 1 — smaller than `k`, which makes r@10 identically 1.0 for **both** arms
   by construction. Exactly one tenant (24 episodes) can produce a discordant
   pair at all. Separately, and independently fatal, the leak-free anchoring rule
   this session established — anchor a golden on a genuine human turn — **cannot
   be applied to C1 at all**: production has zero populated foreign keys between
   a human turn and an episodic memory. This is not "the replication failed"; it
   is "the instrument does not exist and cannot be built from this slice".

2. **Bars 2 and 3 replicate exactly on the current build, nine days on.** The
   hard gate — state-filter exactness — is EXACT: **0 leaks on all 5 tenants**,
   twice, digit for digit across two independent runs.

3. **The Bar-1 SLO regression the drain audit flagged is contention, not drift.**
   The control settles it: the *synthetic* corpus, whose relevant code path
   banked p50 = 32.6 ms, reads **p50 = 213.2 ms today** on the same machine under
   the same contention. A corpus that has not changed cannot have drifted 6.5×.

## 1. The plan's condition was unsatisfiable before this run started

Worth stating plainly, because it is a planning defect and not a measurement
outcome. C1 has **no goldens** — the plan says so itself ("minting them is out of
scope"). What C1 proved was Bar 1 (hot-path SLO), Bar 2 (state-filter exactness,
plus per-episode retrievability as a *reported coverage metric*) and Bar 3
(two-user RLS). None of the three is a paired MemPhant-vs-BM25 accuracy
comparison. "Rerun the C1 probe" therefore could not have produced a paired win
on any day, on any machine, with any corpus. The only question this run could
answer was whether the missing instrument is *constructible*. It is not — §4.

## 2. Extraction (read-only, prereg-bound)

The 2026-07-22 corpus was a one-time gitignored extract and is off disk, so it
was re-extracted. Privacy terms were committed **before** the data was touched
(`f126286c`), following the Track U pattern.

`scripts/c1_prod_extract.py` freezes `syndai.episodic_memories` into a hashed
snapshot under `PGOPTIONS="-c default_transaction_read_only=on"`, issuing only
`SELECT`; `embedding`, `metadata` and `summary` are refused. The derive step is
offline and deterministic from the snapshot, so `--check` re-derives the lock
byte-for-byte. Secret scanning reuses `scripts/github_lane_secrets.py`
unmodified — whole-row drops, pattern name only, never the value.

| | |
|---|---|
| rows | **321** (252 at recon, 270 on 2026-07-22) |
| tenants | 5 |
| recall-visible (`retain`) | **44** |
| rolled-up (`forget`) | 277 |
| `user_correction` (`skip`) | 0 |
| secret-scan drops | **0** |
| empty bodies dropped | 0 |
| snapshot sha256 | `ddc0bc77d273c2da…` |
| corpus sha256 | `04233ddff58d5322…` |

Every count matches the prereg's pinned recon exactly. Bodies live only in the
gitignored `benchmarks/data/private/` and the `~/.memphant-private/c1/` mirror;
`benchmarks/data/c1_prod_episodic.lock.json` is the committed record. Commit
`03aec3ce`.

## 3. Bars 1–3 on the current build

### Bar 2 — state-filter exactness. PASS, and reproduced exactly.

| tenant | visible | distinct | reachable | correctly excluded | leaks |
|---|---|---|---|---|---|
| `d6f83507` | 24 | 24 | 19 (79%) | 222 | **0** |
| `5f0721e7` | 9 | 7 | 0 (0%) | 0 | **0** |
| `cd67a3b2` | 6 | 6 | 6 (100%) | 55 | **0** |
| `eee527c5` | 4 | 4 | 0 (0%) | 0 | **0** |
| `d13f7632` | 1 | 1 | 1 (100%) | 0 | **0** |

Two independent runs (ports 39415 and 39419, separate scratch databases)
produced these numbers identically. The drain contract is verified before any bar
is read: `compiled = 322 == enqueued = 322`, and `assert_worker_queue_empty` on
the bench credential. Against 2026-07-22 the shape is unchanged and the slice has
simply grown (`d6f83507` 17 → 24 visible, 180 → 222 excluded; retrievability on
that tenant 70.6% → 79.2%). The two 0%-retrievability tenants are the same
near-duplicate 16k-char audit-prompt clusters the original run identified — a
property of the data, not a regression.

Artifacts: `docs/build-log/artifacts/c1-episodic/w9-real-prod-provenance.json`,
`…/w9-real-prod-evidence.jsonl`.

### Bar 3 — two-user RLS. PASS, unchanged.

`cargo test -p memphant-store-postgres --test episodic_rls_leakage -- --ignored`
under `scripts/with_scratch_db.sh`: 1 passed. The 2026-07-22 standing note still
holds — the packaged server connects as a superuser login, so on the served HTTP
path RLS is bypassed and isolation rests on the app + tenant-GUC filter.

### Bar 1 — hot-path SLO. RESOLVED-BY-CONTROL: the breach is contention.

The drain audit measured p50 81.8 / p95 108.6 against a banked 32.6 / 37.2 at
load average 72 and called it confounded. It was right, and the confound is
larger than it looked.

| run | corpus | p50 | p95 | loadavg (1m) |
|---|---|---|---|---|
| banked 2026-07-22 | synthetic | 32.6 | 37.2 | not recorded |
| banked 2026-07-22 | real prod | 34.4 | 36.4 | not recorded |
| drain audit 2026-07-30 | synthetic | 81.8 | 108.6 | 72 |
| **W9 control** | **synthetic** | **213.2** | **641.0** | **157 → 138** |
| W9 | real prod (321) | 284.9 | 912.5 | 134 → 120 |
| W9 (repeat) | real prod (321) | 247.9 | 792.6 | 192 → 207 |

The host has **12 CPUs**. Every W9 reading was taken at 10–17× oversubscription
caused by other concurrent sessions on this machine, which this run has no
authority to stop.

The load column is the whole argument. The **synthetic** corpus is byte-identical
to the one that banked 32.6 ms and exercises the same recall path; it reads
213.2 ms today. A 6.5× inflation on an unchanged corpus is not code drift. The
prod/synthetic ratio is the drift-sensitive quantity, and it is stable enough to
be uninformative at this noise level: 34.4/32.6 = 1.06 banked, 284.9/213.2 = 1.34
now, with the two prod readings themselves differing by 15% at similar load.

**What this does and does not establish.** It rules out a 2.5×–8× real
regression: no such regression could leave the synthetic control equally
inflated. It does **not** produce a clean absolute number.

**Status: resolved-by-control.** The open question the drain audit raised — is the
81.8 / 108.6 reading drift or contention? — is answered: contention. The control
is the stronger form of that answer, because it isolates the cause rather than
merely producing a smaller number. A quiet-window re-measurement was scripted
(quiet := 1-minute load < 6 on three consecutive 30 s samples) and abandoned: the
host never fell below load 180 in three hours of watching, with three other
sessions mid-run. It is **deferred, to be scheduled when the box is idle**, and
it is a nice-to-have absolute figure, not a blocker on any bar.

The runner was changed so this is never ambiguous again: `measure_recall_slo`
records `loadavg_1m_before/after` and `cpu_count` into the provenance and returns
rather than raising, with the bar asserted after the artifacts are written. A
latency failure that produces no artifact teaches nothing.

## 4. The golden bank cannot be minted from C1

Four independent blockers. Any one of them is sufficient; all four hold.

**4.1 There is no human-turn → episode edge in production.** The leak-free
anchoring rule requires a genuine human turn as the query and a target the human
had not seen. Measured by read-only `SELECT`:

- `syndai.episodic_memories.run_message_id`: **0 of 321 non-null**.
- `syndai.run_messages.agent_run_id`: **0 of 191 non-null** (`user` role).
- joining the two through `agent_runs.mission_id` yields **0 rows**.

Every candidate join column is empty. The only remaining option is a heuristic
content or timestamp match — which is exactly the class of inference the
convo-lane rule forbids, and forbids for a demonstrated reason (44 of 2,718
`user` records were subagent dispatch prompts, and a cross-session agent-to-agent
message satisfies every naive provenance condition). Anchoring on a *synthesized*
question instead is the Track R defect that contaminated that bank (question →
target coverage 0.396 against a human 0.175–0.287).

**4.2 There is no provenance discriminator on the human turns either.** All 191
`user` rows carry the identical stamp: `source_kind` NULL, `message_kind`
`user_input`, `visibility` `public`, no agent origin, no parent. Syndai runs
canary, dogfood and self-heal automation against production, so some of these are
machine-authored — and nothing in the schema says which. There is no rule to
write.

**4.3 The candidate pool is 63 turns, not 191.** Of 191 `user` rows only **63**
have distinct bodies; 67% are re-runs of the same templated text. Median length
276 characters.

**4.4 The retrieval pools are smaller than `k`.** Recall is tenant-scoped, so the
pool a golden is retrieved from is its own tenant's visible set: 24, 7, 6, 4, 1
distinct bodies. At `k = 10` only the 24-episode tenant can produce a miss at
all; at `k = 5`, 37 of 42 distinct bodies sit in a pool large enough to miss. So
the maximum bank that can discriminate at r@10 is **24 items in a single
tenant** — a bank with no cross-tenant variation, which cannot distinguish a
retrieval property from one user's writing style.

**The power arithmetic.** Exact two-sided McNemar needs **6 discordant pairs all
falling one way** to reach p ≤ 0.05 (2 × 0.5⁶ = 0.031). On a 24-item ceiling that
is a perfect 25% sweep, in one tenant, with no replication available. Even the
best imaginable outcome would be a result nobody should act on — and per the
prereg, widening the extract in pursuit of a bigger bank is explicitly not
authorized. The right response to an underpowered instrument is not to run it.

The paired probe (§step 4 of the task: scoped BM25 vs MemPhant, fused and packed,
r@5/r@10, exact McNemar) was therefore **not run**. Its precondition — goldens
clearing a leakage bar — is unreachable, so running it would have produced a
number with the shape of evidence and none of the content.

## 5. Ownership condition (d): not satisfiable on C1; re-scope it

C1 is a **correctness** instrument. It proves state-filter exactness, RLS
isolation and hot-path latency on real production rows, and it still does. It was
never an accuracy instrument and cannot be converted into one: production holds
44 recall-visible episodes with no human-turn linkage.

What would actually settle (d), in ascending cost:

1. **Re-scope the condition to the convo lane.** The convo-lane extractor already
   implements the human-turn rule against a corpus with real provenance stamps
   and real volume. That is the same claim — a paired win on production-shaped
   conversational data — measured on an instrument that exists.
2. **Instrument production, then wait.** Populating
   `episodic_memories.run_message_id` (the column exists and is empty) would
   create the human-turn → episode edge. It buys nothing today; it makes the C1
   accuracy question answerable in some future quarter at some future volume.
3. **Accept a correctness-only C1** and let the ownership answer rest on the
   coding and convo lanes, with C1 cited for what it proves.

Option 1 is the only one that answers the question this quarter. **Recommendation:
amend the plan's condition (d) to name the convo lane, and record C1 as
correctness-only — permanently, not pending.**

## 6. The zero-FK finding is a product observation, not just an eval obstacle

Production conversations are not linked to the memories they produced. That is
worth stating on its own terms, because it decides whether the convo lane can be
linked later. Three distinct things are going on, and only one is a bug.

**6.1 `episodic_memories.run_message_id` is dead schema, not a write-path gap.**
In `backend/src/features/memory/models.py:402` it is declared
`Mapped[UUID | None] = mapped_column(nullable=True)` — a bare nullable UUID with
**no `ForeignKey`**, unlike its neighbours `project_id` and `mission_id`, which
both carry real FK constraints. The sole construction site,
`EpisodicMemoryService` at `backend/src/features/memory/episodic_service.py:131`,
passes `user_id`, `l0_agent_id`, `project_id`, `mission_id`, `content`,
`summary`, `metadata_`, `trust_level`, `source_kind`, `importance_score` and
`idempotency_key` — and not `run_message_id`. There is no writer to fix. The
column was declared and never wired, which is why prod shows 0/321.

**6.2 `memory_references` is the real provenance table, and it is empty in
production.** This one *is* a write-path gap, and it is the interesting one.
`MemoryReference` (`models.py:640`) is properly built: `run_message_id` is a
NOT NULL FK to `run_messages` with `ON DELETE CASCADE`, `memory_type` is
CHECK-constrained to include `'episodic'`, and there is a unique index on
`(run_message_id, memory_type, memory_id)`. Its docstring describes exactly the
edge the eval wants: "which memory an agent referenced in a response". Measured
in production by read-only `SELECT`: **0 rows, 0 episodic references, 0 distinct
messages**. The plumbing is correct, complete, and has never been written to.

**6.3 `run_messages.agent_run_id` at 0/191 is by design, not a gap.** It carries a
*partial* index (`postgresql_where=text("agent_run_id IS NOT NULL")`,
`run_message_models.py:99`) because it identifies child-agent messages. NULL on a
top-level turn is correct. It supplies no join, but it is not missing anything.

**Bearing on the convo lane, with one caveat that matters.** Populating
`memory_references` on the write path is a small, well-scoped change — the agent
already knows which memories it injected — and it would create a real, FK-backed
human-turn → memory edge. But it would record what the *incumbent retriever
surfaced*, not what is actually relevant. Using it directly as retrieval ground
truth is circular: the bank would be scored against the system's own output and
would flatter any arm that resembles the incumbent. It is a legitimate
**candidate generator** feeding human or adjudicated relevance labels; it is not
an oracle, and it should never be treated as one.

## Verification

- `python3 -m pytest tests/ -q` — 1065 passed, 15 skipped, **1 failed**:
  `test_public_launch_gate.py::test_public_sota_claim_policy_is_explicit_and_bare_claims_are_guarded`,
  which shells out to `npm test` in `web/`. `web/node_modules` does not exist in
  this worktree, so Playwright is not installed: `sh: playwright: command not
  found`, exit 127. Environmental, not a code failure.
- `cargo test --workspace` — **1 failed**:
  `memphant-core::contextual_chunk_write::recall_chunk_renders_matched_window_plus_neighbour`.
  Attributed by `git bisect run` over `bf2c87c3..15b2a647` to **`f67f2b2a`**
  ("fix: let a partially chunk-rendered item emit its whole body"), which landed
  before this branch's work began. This session's diff touches no Rust. Filed as
  separate work; not fixed here.
- `cargo test -p memphant-store-postgres --test episodic_rls_leakage -- --ignored`
  under `with_scratch_db.sh` — 1 passed.
- Spend: **$0**. The extract is a `SELECT`; the bench is deterministic retrieval
  on a local scratch database with a local embedder. No provider call of any
  kind.
- Production was read and never written: every statement ran under
  `default_transaction_read_only = on`, and the only verb issued was `SELECT`.
- Leak check: 887 needles (every distinct body prefix and every `id`, `user_id`,
  `l0_agent_id`, `project_id`, `mission_id` and `idempotency_key` in the private
  corpus) searched across every `git ls-files` path — **0 tracked files match**.
  `tests/test_episodic_lane_run_memphant.py` additionally asserts, without the
  private corpus, that no committed C1 real-prod artifact contains a UUID.

## Artifacts

- `docs/build-log/artifacts/c1-episodic/w9-real-prod-provenance.json` + `…-evidence.jsonl`
- `docs/build-log/artifacts/c1-episodic/w9-synthetic-control-provenance.json` + `…-evidence.jsonl`
- `benchmarks/data/c1_prod_episodic.lock.json`
- Commits: `f126286c` (prereg) `03aec3ce` (extract mechanism + lock) `bf23c3bd` (bars).
