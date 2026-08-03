# HorizonBench Belief-Update Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a fail-closed, gold-quarantined HorizonBench sample pilot over full-context, MemPhant Fast, and selective Deep using one fixed reader under the approved $25 ceiling.

**Architecture:** One stdlib Python runner owns acquisition, prompt construction, runtime evidence, paid execution, and paired analysis while reusing `gate_runtime.py`, `run_reader.ReaderCli`, and `provider_attempts.py`. PostgreSQL remains the only substrate; dataset and paid caches remain outside tracked source or in the existing gitignored artifact namespace.

**Tech Stack:** Python standard library, pytest, packaged Rust server/worker/CLI, PostgreSQL scratch database, Hugging Face Dataset Viewer, OpenRouter through Doppler.

## Global Constraints

- Accuracy/UX > cost > performance/latency > security; existing trust boundaries remain intact.
- Only `id`, `user_id`, `generator`, `conversation`, and `options` may enter runtime or prompts.
- The mental-state graph is never acquired.
- The sample has exactly ten terminal rows per arm; any omission or duplicate blocks scoring.
- Reader spend is at most $22 and Deep worst-case liability is at most $3, for a combined $25 ceiling.
- Paid commands alone run through `doppler run --project syndai --config dev -- ...`.
- The sample is a diagnostic kill gate; it cannot move a SOTA checkbox.
- No new dependency, service, table, product default, or benchmark-specific mutation path.

---

### Task 1: Seal acquisition and prompt quarantine

**Files:**
- Create: `scripts/run_horizonbench.py`
- Create: `tests/test_horizonbench_contract.py`
- Create: `benchmarks/manifests/horizonbench.sample.v1.json`

**Interfaces:**
- Consumes: Dataset Viewer `sample/test` rows and the pinned dataset revision.
- Produces: `fetch_sample(output: Path) -> dict`, `parse_sessions(text: str) -> list[dict]`, `prompt_item(row: dict) -> dict`, and an immutable ten-ID lock.

- [ ] **Step 1: Write the failing quarantine and completeness tests**

  Cover canonical options parsing, official conversation segmentation,
  rejection of missing/duplicate IDs, and prove that serialized prompt items
  omit all five scoring-only fields and their sentinel values.

- [ ] **Step 2: Run the focused test and confirm RED**

  Run: `python3 -m pytest tests/test_horizonbench_contract.py -q`

  Expected: import failure because `scripts/run_horizonbench.py` does not exist.

- [ ] **Step 3: Implement the minimum pure acquisition layer**

  Use `urllib.request`, `json`, and `hashlib`; validate `sample/test`, exactly ten
  unique IDs, ten unique sample users, required field types, five distinct
  option letters, and non-empty parsed sessions. Write canonical JSONL
  atomically and return its SHA-256/census. Never return gold from
  `prompt_item`.

- [ ] **Step 4: Run focused tests and commit**

  Run: `python3 -m pytest tests/test_horizonbench_contract.py -q`

  Commit: `eval: seal HorizonBench sample inputs`

### Task 2: Build the $0 MemPhant evidence bank

**Files:**
- Modify: `scripts/run_horizonbench.py`
- Modify: `tests/test_horizonbench_contract.py`
- Create: `docs/build-log/artifacts/horizonbench-pilot/preregistration.json`

**Interfaces:**
- Consumes: canonical sample JSONL, packaged binaries, scratch PostgreSQL.
- Produces: `build_runtime_rows(...) -> list[dict]` and a Fast evidence JSONL with one complete row per expected ID.

- [ ] **Step 1: Write failing tests for runtime payloads and fail-closed rows**

  Assert isolated item-scoped context IDs, chronological RFC3339 retain
  timestamps, byte-bounded non-empty bodies, options-only recall queries,
  rejection of degraded/empty evidence, and exact expected-ID completion.

- [ ] **Step 2: Confirm RED**

  Run: `python3 -m pytest tests/test_horizonbench_contract.py -q`

  Expected: missing runtime-row functions.

- [ ] **Step 3: Implement by composing existing runtime helpers**

  Reuse `gate_runtime.reexec_through_scratch_db`, `Server`, `ApiClient`,
  `episode_retain_payload`, `drain_worker`, and the public `/v1/recall` path.
  Retain released sessions verbatim as `source_kind=user` episodes, drain once,
  request Fast evidence at fixed `k=20`/`budget_tokens=16384`, and record
  bodies, citations, latency, trace identity, and degraded state.

- [ ] **Step 4: Run the focused tests, build release binaries if absent, and execute the $0 screen**

  Run the sample acquisition and Fast evidence command without Doppler. Stop
  before paid work if any preregistered predicate fails.

- [ ] **Step 5: Register and commit the $0 evidence**

  Add the preregistration/result to the evidence-contract registry if it is
  decisional; otherwise record it explicitly as non-decisional construction
  proof. Commit: `eval: qualify HorizonBench Fast evidence`

### Task 3: Run the capped paired reader pilot

**Files:**
- Modify: `scripts/run_horizonbench.py`
- Modify: `tests/test_horizonbench_contract.py`
- Create: `docs/build-log/artifacts/horizonbench-pilot/authorization.json`

**Interfaces:**
- Consumes: frozen source and Fast evidence hashes plus the approved owner cap.
- Produces: exact terminal JSONL rows for `full_context`, `fast`, and `selective_deep`, durable paid-attempt journals, and cached replies.

- [ ] **Step 1: Write failing authorization, routing, and resume tests**

  Assert authorization hash binding, $22 reader reservation, at most ten Deep
  calls/$3 worst-case liability, same reader/model/decoding across arms, Deep
  invocation only after a gold-blind insufficiency response, refusal to score
  non-completed Deep, duplicate-free resume, and terminal error rows.

- [ ] **Step 2: Confirm RED**

  Run: `python3 -m pytest tests/test_horizonbench_contract.py -q`

- [ ] **Step 3: Implement the minimal paid layer**

  Import `ReaderCli`, `parse_reader_output`, `open_campaign_ledger`, and spend
  restoration. Use `anthropic/claude-opus-4.5`, temperature zero, strict reader
  JSON, provider pin `anthropic`, and maximum output 256 tokens. The selective
  arm reuses a non-abstaining Fast answer or performs one explicit Deep recall
  followed by one final reader call. Persist after every terminal row.

- [ ] **Step 4: Execute the paid pilot through Doppler**

  Wrap only the secret-consuming command. Stop immediately on a ceiling,
  incomplete ledger, provider mismatch, unpriced attempt, or failed Deep
  envelope.

- [ ] **Step 5: Verify paid accounting and commit immutable compact proof**

  Keep paid response bodies/cache gitignored. Commit only authorization,
  attempt census/hashes, compact row results, and exact settled/unsettled cost.
  Commit: `eval: run capped HorizonBench paired pilot`

### Task 4: Analyze, update the ledger, and publish

**Files:**
- Modify: `scripts/run_horizonbench.py`
- Modify: `tests/test_horizonbench_contract.py`
- Create: `docs/build-log/2026-08-03-horizonbench-belief-update-pilot.md`
- Create: `docs/build-log/artifacts/horizonbench-pilot/result.json`
- Modify: `benchmarks/manifests/evidence_contract_registry.json`
- Modify: `docs/build-log/artifacts/evidence-contract-retrofit.json`
- Modify: `docs/superpowers/specs/memphant/STATUS.md`

**Interfaces:**
- Consumes: complete frozen arm rows and scoring-only gold.
- Produces: paired item results, user-clustered diagnostic intervals, stale-distractor counts, prompt-token/cost/latency accounting, and one honest advance/stop verdict.

- [ ] **Step 1: Write failing analysis tests**

  Cover exact ID joins, scoring-only gold join, evolved/static metrics,
  distractor selection, paired gains/losses, deterministic user-cluster
  bootstrap, and the preregistered pilot verdict.

- [ ] **Step 2: Confirm RED, implement analysis, and rerun GREEN**

  Run: `python3 -m pytest tests/test_horizonbench_contract.py -q`

- [ ] **Step 3: Run repository evidence and focused gates**

  Run:

  ```sh
  python3 scripts/check_evidence_contract.py
  python3 scripts/instrument_power.py --check
  python3 -m pytest tests/test_horizonbench_contract.py tests/test_run_reader_contract.py tests/test_gate_runtime.py -q
  cargo fmt --check
  ```

- [ ] **Step 4: Update STATUS only with the proven boundary**

  A failed or merely diagnostic pilot records the blocker without moving SOTA.
  A passing pilot records authorization for a future powered plan, not SOTA.

- [ ] **Step 5: Commit, push main, and verify CI**

  Commit: `docs: record HorizonBench pilot verdict`. Push the authorized main
  branch and poll the exact GitHub Actions run no more often than every two
  minutes.

