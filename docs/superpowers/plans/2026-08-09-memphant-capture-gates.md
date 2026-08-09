# MemPhant Capture — Gates & Ledger Wire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the decision-ready increment of `memphant capture` — apply the outcome ledger migration, wire the harness→ledger telemetry path, and run the $0 offline census/validation that greenlights or kills the whole capture build — ending at a go/no-go verdict.

**Architecture:** Three parts, cheapest-kill-first. Part A applies migration 010. Part B (the kill gate) assembles a census dataset from Syndai's `coding_execution_attempt_events` and measures whether `correction → distinct-later-same-scope-task` chains exist and whether outcome-gated minting beats blind minting on precision. Part C wires `memphant capture outcome` → `/v1/task-outcomes` (the one live consumer, useful regardless of the gate). **Stage 1b (the capture engine) is a SEPARATE plan, written only if Part B returns GO.**

**Tech Stack:** Rust (`memphant-cli`, `memphant-core`, `memphant-store-postgres`), Python 3 stdlib (census script, matching `scripts/gate_*.py` conventions), Postgres (Finn shared DB, scoped to `memphant.*`).

**Spec:** `docs/superpowers/specs/2026-08-09-memphant-capture-design.md` (twice-reviewed, owner-approved).

## Global Constraints

- **Priority order: accuracy > cost > speed.** Never trade accuracy for cheaper/faster.
- **Zero new schema/route** for episodes/chat/outcomes (the resource/docs lane is out of scope for this plan).
- **Committed artifacts carry hashes/counts/offsets only — never transcript bodies** (flow-doc rule; census outputs are aggregate).
- **No `regex` dependency in `memphant-core`** (core refuses it — `service.rs:6691`). Not relevant to this plan (no masking here), but holds.
- **Census is `UNTESTABLE`-honest:** if the chains don't exist, the gate returns NO-GO and nothing downstream is built. This is a feature.
- **Shared-DB discipline:** all DB work scoped to `memphant.*`; migration application to Finn is off-peak and verified; never touch the `schema_migrations` RLS advisor.
- **Full harness gate before "done":** `python3 scripts/check_evidence_contract.py`, `scripts/check_spec_drift.py`, `scripts/instrument_power.py --check`, `cargo fmt --check`, `cargo clippy --all-targets --all-features -- -D warnings`, `cargo test --workspace --all-targets --all-features`, `cargo test --doc`, plus the scratch-DB `--ignored` tier via `scripts/with_scratch_db.sh`.
- **Census decisional artifact** carries an `evidence_contract` block and is registered in `benchmarks/manifests/evidence_contract_registry.json` (a new decisional artifact in neither list fails CI).

---

## Part A — Apply migration 010 (Stage 0-pre)

The `task_outcome`/`task_memory_event` tables exist only in the repo; Finn's `schema_migrations` tops out at `009`. Nothing in Part B/C works until 010 is applied.

### Task A1: Verify migration 010 applies cleanly to a scratch DB

**Files:**
- Read: `memphant_migrations/versions/20260808_010_task_outcome_ledger.sql`
- Use: `scripts/with_scratch_db.sh`, `scripts/apply_memphant_migrations.py`

**Interfaces:**
- Produces: confirmation that 010 applies on top of 009 and the two tables + indexes exist.

- [ ] **Step 1: Apply all migrations to a fresh scratch DB and assert 010's tables exist**

Run:
```bash
bash scripts/with_scratch_db.sh postgres://localhost MEMPHANT_TEST_DATABASE_URL \
  psql "$MEMPHANT_TEST_DATABASE_URL" -c "\dt memphant.task_outcome" -c "\dt memphant.task_memory_event"
```
Expected: both tables listed; no migration error.

- [ ] **Step 2: Assert the ledger's uniqueness + FK constraints landed**

Run:
```bash
bash scripts/with_scratch_db.sh postgres://localhost MEMPHANT_TEST_DATABASE_URL \
  psql "$MEMPHANT_TEST_DATABASE_URL" -c "\d memphant.task_memory_event"
```
Expected: `unique (tenant_id, task_id, memory_unit_id, event, attribution)` and the FK to `task_outcome` (cascade) are present.

- [ ] **Step 3: Dry-run the migration against the (invalid) prod URL to confirm the plan output**

Run: `python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run`
Expected: lists `20260808_010_task_outcome_ledger.sql` as pending.

- [ ] **Step 4: Commit the verification note**

Record in the flow doc that 010 is verified scratch-clean and pending on Finn.
```bash
git add docs/flows/outcome-coupled-evolution.md
git commit -m "chore: verify migration 010 applies clean; pending on Finn"
```

> **Applying to Finn is a human-gated operational step** (off-peak, `memphant.*` only, verified). Do NOT auto-apply to the shared DB in an agent run — surface it for the owner.

---

## Part B — The census/validation kill gate (Stage 1a)

One Python harness, two modes: **census** (do correction→later-same-scope chains exist at all?) and **validation** (does outcome-gated minting beat blind minting on precision?). Read-only against the Syndai **dev** DB. Follows `scripts/gate_*.py` conventions.

**Data source (verified read-only, 2026-08-09):** Syndai dev DB (Supabase), `syndai` schema, reached via `doppler run --config dev`. Confirmed counts: **317 distinct attempts across 183 runs / 6 repos**, 1,434 `user` event rows, outcomes `completed`=103 / `failed`=161 / `cancelled`=22. This is enough for a census.

**Real schema (do NOT assume the earlier draft's shape):**
- `syndai.coding_execution_attempt_events`: `{id, coding_run_id, attempt_id, sequence, event_type, subtype, payload (JSONB — turn content), occurred_at}`. **Turn text lives in `payload`**, and a *user turn* is `event_type='user'` (the correction-candidate rows). `assistant` and structured `message_*/turn_*/tool_execution_*` events also exist — the nominator only reads `event_type='user'`.
- `syndai.coding_runs`: `{coding_repository_id (=repo scope), current_phase, pr_status, terminal_summary (JSONB), validation_iteration}`. **Outcome = `current_phase`**: map `completed → passed`, `failed`/`cancelled → not_passed`. There is NO `validator_status` column.
- Join: events → run via `coding_run_id` → `coding_repository_id` (scope) + `current_phase` (outcome).

**Secrets/connection seam:** the script is doppler-agnostic — it reads `CENSUS_DATABASE_URL` (falling back to `DATABASE_URL`) from the env; the operator runs it wrapped in `doppler run --config dev -- …`. No secret ever enters the script or a committed file.

### Task B1: Assemble the census dataset from `syndai.coding_execution_attempt_events`

**Files:**
- Create: `scripts/capture_census_dataset.py`
- Create: `benchmarks/data/capture_census.jsonl` (gitignored — carries turn text)
- Create: `benchmarks/data/capture_census.stats.json` (committed — counts only)
- Reference: `scripts/gate_common.py` (row/env helpers)

**Interfaces:**
- Produces: a per-attempt record `{attempt_id, run_id, repo_scope, started_at, ended_at, outcome, user_turns:[{sequence, text}]}` where `repo_scope` = `coding_repository_id`, `outcome ∈ {passed, not_passed}` (from `current_phase`), and `user_turns` are the `event_type='user'` events' extracted `payload` text, in `sequence` order.

- [ ] **Step 1: Write the failing test for `normalize_attempt` (pure, no DB)**

`scripts/tests/test_capture_census_dataset.py`:
```python
from capture_census_dataset import normalize_attempt, phase_to_outcome
def test_phase_maps_to_binary_outcome():
    assert phase_to_outcome("completed") == "passed"
    assert phase_to_outcome("failed") == "not_passed"
    assert phase_to_outcome("cancelled") == "not_passed"
def test_normalize_attempt_extracts_user_turns_and_scope():
    raw = {"attempt_id":"a1","coding_run_id":"r1","repo_scope":"repo1",
           "current_phase":"completed","started_at":"2026-07-06T15:44:21+00:00","ended_at":"2026-07-06T15:50:00+00:00",
           "events":[{"sequence":4,"event_type":"user","payload":{"text":"use pnpm not npm"}},
                     {"sequence":5,"event_type":"assistant","payload":{"text":"ok"}}]}
    rec = normalize_attempt(raw)
    assert rec["repo_scope"]=="repo1" and rec["outcome"]=="passed"
    assert rec["user_turns"]==[{"sequence":4,"text":"use pnpm not npm"}]
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python3 -m pytest scripts/tests/test_capture_census_dataset.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `phase_to_outcome`, `normalize_attempt`, and a read-only `main()`**

`phase_to_outcome(p)` = `"passed" if p=="completed" else "not_passed"`. `normalize_attempt(raw)` keeps only `event_type=='user'` events, extracts `payload["text"]` (payload may be a JSON string or dict — handle both; skip events with no text), returns the record shape above. `main()` reads `CENSUS_DATABASE_URL` or `DATABASE_URL` from env, runs ONE read-only SQL that joins `syndai.coding_execution_attempt_events` → `syndai.coding_runs`, groups events by `attempt_id`, and writes `benchmarks/data/capture_census.jsonl`. **Never** writes to the DB.

- [ ] **Step 4: Run the test, verify it passes**

Run: `python3 -m pytest scripts/tests/test_capture_census_dataset.py -q`
Expected: PASS (both).

- [ ] **Step 5: Generate the dataset from dev and record stats**

Run: `cd /Users/sidsharma/Syndai-capture-census && doppler run --config dev -- python3 /Users/sidsharma/Memphant/scripts/capture_census_dataset.py --out /Users/sidsharma/Memphant/benchmarks/data/capture_census.jsonl`
Expected: prints `attempts=N with_scope=N with_outcome=N user_turns=N`. With the verified data, `attempts≈300+`, `with_scope` and `with_outcome` both near-total. If either is ~0, STOP — legitimate `UNTESTABLE` NO-GO.

- [ ] **Step 6: Gitignore raw, commit script + stats only**

Add `benchmarks/data/capture_census.jsonl` and `capture_census_labels.jsonl` to `.gitignore`. `.stats.json` carries counts only (no text).
```bash
git add scripts/capture_census_dataset.py scripts/tests/test_capture_census_dataset.py benchmarks/data/capture_census.stats.json .gitignore
git commit -m "feat: assemble capture census dataset from syndai execution-attempt events (read-only dev mine)"
```

### Task B2: Detect `correction → distinct-later-same-scope-task` chains (census mode)

**Files:**
- Create: `scripts/capture_census.py`
- Reference: the corrections-nominator grammar from the spec (§4.1); `scripts/gate_mine_goldens.py` (verbatim-span, drop-not-fabricate discipline)

**Interfaces:**
- Consumes: `benchmarks/data/capture_census.jsonl` (from B1) — records with `{attempt_id, repo_scope, started_at, ended_at, outcome, user_turns}`.
- Produces: `census_result = {attempts, corrections_nominated, chains_found, chain_rate}` where a *chain* = a correction nominated in attempt X whose same-`repo_scope` has a distinct later attempt Y (`started_at_Y > ended_at_X`) with `outcome == "passed"`.

- [ ] **Step 1: Write the failing test for chain detection**

`scripts/tests/test_capture_census.py`:
```python
from capture_census import find_chains, nominate_corrections
def test_correction_then_clean_later_same_scope_is_a_chain():
    ds = [
      {"attempt_id":"x","repo_scope":"r","started_at":"2026-07-01T00:00:00+00:00","ended_at":"2026-07-01T01:00:00+00:00",
       "outcome":"not_passed","user_turns":[{"sequence":3,"text":"no, use pnpm not npm"}]},
      {"attempt_id":"y","repo_scope":"r","started_at":"2026-07-02T00:00:00+00:00","ended_at":"2026-07-02T01:00:00+00:00",
       "outcome":"passed","user_turns":[{"sequence":1,"text":"add a route"}]},
    ]
    chains = find_chains(ds)
    assert len(chains) == 1 and chains[0]["correction_attempt"] == "x" and chains[0]["clean_attempt"] == "y"

def test_no_later_task_same_scope_is_not_a_chain():
    ds = [{"attempt_id":"x","repo_scope":"r","started_at":"2026-07-01T00:00:00+00:00","ended_at":"2026-07-01T01:00:00+00:00",
           "outcome":"not_passed","user_turns":[{"sequence":3,"text":"no, use pnpm not npm"}]}]
    assert find_chains(ds) == []

def test_conversational_non_correction_user_turn_is_not_nominated():
    assert nominate_corrections([{"sequence":1,"text":"thanks, that looks great"}]) == []
```

- [ ] **Step 2: Run, verify all fail**

Run: `python3 -m pytest scripts/tests/test_capture_census.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `nominate_corrections` (deterministic) + `find_chains`**

`nominate_corrections(user_turns)` fires on prohibition/imperative turns ("no", "don't", "never", "stop", "use X not Y", "revert", "actually") — precision-first, reject conversational praise/ack. `find_chains(ds)` groups by `repo_scope`, sorts by `started_at`, and for each attempt with a nominated correction finds a distinct later same-scope attempt with `outcome == "passed"` (`started_at_Y > ended_at_X`).

- [ ] **Step 4: Run, verify pass**

Run: `python3 -m pytest scripts/tests/test_capture_census.py -q`
Expected: PASS (both).

- [ ] **Step 5: Run the census over the real dataset — THE GATE**

Run: `python3 scripts/capture_census.py --data benchmarks/data/capture_census.jsonl --mode census`
Expected: prints `attempts=N corrections=C chains=K chain_rate=R`.
- **K ≈ 0 → NO-GO.** Outcome-gating is `UNTESTABLE` on available data. Record it; the capture engine (Stage 1b) is not built. Stop the plan here with an honest kill.
- **K meaningfully > 0 → proceed to B3** (measure whether the gate actually improves precision).

- [ ] **Step 6: Commit the census script + result**

```bash
git add scripts/capture_census.py scripts/tests/test_capture_census.py
git commit -m "feat: correction->later-same-scope chain census (Stage 1a mode 1)"
```

### Task B3: Validation mode — outcome-gated vs blind minting precision

**Files:**
- Modify: `scripts/capture_census.py` (add `--mode validation`)
- Create: `docs/build-log/artifacts/capture-census/result.json` (the decisional artifact)

**Interfaces:**
- Consumes: the chains from B2 + a small hand-labeled truth set (which nominated corrections are *genuine* durable corrections vs conversational false positives).
- Produces: `{blind_precision, gated_precision, delta, blind_false_positives_later_reviolated}` with a Wilson lower bound on each precision.

- [ ] **Step 1: Write the failing test for the precision computation**

`scripts/tests/test_capture_census.py` (add):
```python
from capture_census import precision_wilson
def test_precision_wilson_lower_bound_is_conservative():
    p, lo = precision_wilson(true_positives=8, total=10)
    assert p == 0.8 and lo < 0.8 and lo > 0.4
```

- [ ] **Step 2: Run, verify it fails**

Run: `python3 -m pytest scripts/tests/test_capture_census.py::test_precision_wilson_lower_bound_is_conservative -q`
Expected: FAIL.

- [ ] **Step 3: Implement `precision_wilson` + the validation join**

`precision_wilson(tp, total)` returns `(point, wilson_lower_bound_95)`. Validation: **blind** precision = fraction of all nominated corrections that are genuine (vs the truth set); **gated** precision = fraction of *chain-backed* nominated corrections that are genuine. Also count blind-minted corrections whose same-scope was later re-violated (blind false positives the gate would have caught).

- [ ] **Step 4: Run, verify pass**

Run: `python3 -m pytest scripts/tests/test_capture_census.py -q`
Expected: PASS (all).

- [ ] **Step 5: Produce the labeled truth set (the real critical-path cost)**

Hand-label the nominated corrections from B2's chains as genuine/false (this is the labeling work the spec flagged). Store labels as `benchmarks/data/capture_census_labels.jsonl` (gitignored — carries text); commit only the count + sha.

- [ ] **Step 6: Run validation — THE SECOND GATE**

Run: `python3 scripts/capture_census.py --data benchmarks/data/capture_census.jsonl --mode validation --out docs/build-log/artifacts/capture-census/result.json`
Expected: `gated_precision - blind_precision` reported with CIs.
- **Gated meaningfully beats blind (lower bound above blind's point) → GO.** Build Stage 1b.
- **No lift → NO-GO.** Outcome-gating doesn't earn its complexity; capture stays Stage 0 (ledger) only.

- [ ] **Step 7: Register + validate the decisional artifact**

Add `docs/build-log/artifacts/capture-census/result.json` to `benchmarks/manifests/evidence_contract_registry.json` (move `pending`→`contracted`), give it an `evidence_contract` block, then:
```bash
python3 scripts/check_evidence_contract.py --report
python3 scripts/check_evidence_contract.py
git add scripts/capture_census.py scripts/tests/test_capture_census.py docs/build-log/artifacts/capture-census/result.json benchmarks/manifests/evidence_contract_registry.json
git commit -m "feat: outcome-gated vs blind minting validation (Stage 1a mode 2) + evidence contract"
```

---

## Part C — Harness → ledger wire (Stage 0)

Independent of the gate (outcome telemetry is the one live consumer regardless). Adds `memphant capture outcome` to the CLI.

### Task C1: `memphant capture outcome` subcommand + task-outcomes client

**Files:**
- Create: `crates/memphant-cli/src/capture.rs`
- Modify: `crates/memphant-cli/src/main.rs:35` (dispatch arm), `:13-14` (module decl)
- Test: `crates/memphant-cli/tests/capture_outcome.rs`
- Reference: `crates/memphant-cli/src/main.rs:118` (`http_verbs::run` HTTP-client pattern: `MEMPHANT_URL`, `MEMPHANT_API_KEY` bearer, `Idempotency-Key` header)

**Interfaces:**
- Consumes: `MEMPHANT_URL`, `MEMPHANT_API_KEY` env; a `TaskOutcome` JSON payload on stdin or `--file`.
- Produces: `capture::run(args: &[String]) -> ExitCode`; POSTs to `/v1/task-outcomes` with a deterministic `Idempotency-Key = "task-outcome:{task_id}:{transcript_sha256}"`.

- [ ] **Step 1: Write the failing test — deterministic idempotency key**

`crates/memphant-cli/tests/capture_outcome.rs`:
```rust
use memphant_cli::capture::idempotency_key_for_outcome;
#[test]
fn outcome_idempotency_key_is_task_and_transcript_bound() {
    let k = idempotency_key_for_outcome("task-1", "a".repeat(64).as_str());
    assert_eq!(k, format!("task-outcome:task-1:{}", "a".repeat(64)));
}
```

- [ ] **Step 2: Run, verify it fails**

Run: `cargo test -p memphant-cli --test capture_outcome -- --nocapture`
Expected: FAIL (module/function missing).

- [ ] **Step 3: Implement `capture.rs` with `idempotency_key_for_outcome` + `run`**

`idempotency_key_for_outcome(task_id, transcript_sha256) -> String` returns `format!("task-outcome:{task_id}:{transcript_sha256}")`. `run` parses `outcome` subcommand, reads the payload, and POSTs via the same `ureq` + bearer pattern as `http_verbs`. Add `mod capture;` at `main.rs:14` and the dispatch arm `if args.first() == Some("capture") { return capture::run(&args[1..]); }` at `main.rs:35`.

- [ ] **Step 4: Run, verify pass**

Run: `cargo test -p memphant-cli --test capture_outcome`
Expected: PASS.

- [ ] **Step 5: End-to-end against a scratch server (idempotent replay)**

Add an `#[ignore]` test that POSTs the same outcome twice against a scratch DB and asserts the second is a replay (no duplicate row), mirroring `http_verbs` E2E patterns. Run via `scripts/with_scratch_db.sh`.

- [ ] **Step 6: Commit**

```bash
git add crates/memphant-cli/src/capture.rs crates/memphant-cli/src/main.rs crates/memphant-cli/tests/capture_outcome.rs
git commit -m "feat: memphant capture outcome subcommand -> /v1/task-outcomes"
```

### Task C2: Document the Syndai/Pi/OpenCode harness call

**Files:**
- Modify: `README.md` (or `docs/`) — one `bash` block showing a harness POSTing its run outcome post-run.

- [ ] **Step 1: Write the usage doc**

A minimal `memphant capture outcome --file outcome.json` example with the `TaskOutcome` field list (`task_id, harness_id, model_id, completion_status, validator_status, tool_count, ..., transcript_sha256`). Note `validator_status='not_run'` for validator-less tasks (chats).

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: memphant capture outcome usage for harnesses"
```

---

## Go/No-Go verdict (end state of this plan)

After Part B:
- **Part B census (B2) K≈0 → NO-GO(dataset):** outcome-gating untestable on available data; ship Stage 0 (ledger) only; do not build the capture engine.
- **Part B validation (B3) no lift → NO-GO(mechanism):** gate doesn't beat blind; ship Stage 0 only.
- **Both pass → GO:** write the Stage 1b plan (capture engine: CC adapter, cursor+resume, edge secret-mask, server-side nominators, `verify_binding()`), per spec §4/§8.

Part C ships regardless of the verdict.

## Self-review notes

- Spec coverage: this plan covers Stage 0-pre (§8), Stage 1a census+validation (§8, §10), Stage 0 (§8). Stage 1b/1c/2 are explicitly deferred to follow-on plans gated on the verdict — not gaps.
- The census input reality (Syndai `coding_execution_attempt_events`, not a ready file) is handled in B1 as the real first task; an empty scope/outcome join is a legitimate early NO-GO.
- Idempotency key naming is consistent (`idempotency_key_for_outcome` / `task-outcome:{task_id}:{sha256}`) across C1 test and implementation.
