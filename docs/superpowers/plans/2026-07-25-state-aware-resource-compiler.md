# State-Aware Resource Compiler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile provenance-correct resource/tool observations into canonical current state and prove a statistically valid LongMemEval-V2 Fast/Deep comparison without allowing cumulative campaign liability above USD 200.

**Architecture:** Generalize the existing episode structured-state path into source-neutral evidence slices and observations, then fold observations once against tenant-local active state through the existing mutation/citation machinery. Extend the existing append-only provider-attempt journal into the sole campaign-wide authorization and spend authority; the LME runner may issue paid calls only after a no-model official-profile census satisfies the frozen admission equation.

**Tech Stack:** Rust 2024, Tokio, serde/serde_json, SHA-256, Python 3.11+, pytest, PostgreSQL scratch databases, official LongMemEval-V2 harness.

## Global Constraints

- The campaign hard ceiling is exactly `200_000_000_000` nano-USD across every screen and provider path; amounts finer than one nano-USD round upward.
- Opening liability is exactly `4_258_002_400` nano-USD until an append-only receipt-bound reconciliation event reduces it.
- Retain at least `10_000_000_000` nano-USD unallocated contingency.
- Paid admission requires `4.2580024 + C + 451 * (2R + S) + 10 <= 200`, where `C` includes construction retries, `R` is one official 200K reader-plus-judge arm, and `S` covers an entire Deep recall including all turns and retries.
- No provider credential access or call occurs before the campaign journal durably reserves the cache miss.
- A validated same-packet cache hit creates no paid attempt; closed packets and alternate journal/cache/output paths fail before cache access.
- Provider-visible extraction contains no tenant-local resource, episode, unit, or chunk UUID.
- Resource evidence always preserves exact UTF-8 parent-resource spans and `source_resource_id`; episode evidence remains restricted to accepted user/user-agent ranges.
- Model output discovers observations only. Tenant-local active state, target IDs, canonical spans, mutations, citations, and receipts remain trusted deterministic code.
- No query text, answer, question ID, question type, or oracle field may influence construction or its cache key.
- Do not add a database, retrieval engine, compatibility shim, benchmark-only memory store, or provider dependency.
- Use TDD: each implementation task begins with a focused failing test and ends with a focused commit and clean worktree.
- No paid/model execution is part of Tasks 1-5; Task 6 may authorize it only if every free gate and the admission inequality pass.

---

### Task 1: Campaign-Wide Attempt Ledger and Closure Authority

**Files:**
- Modify: `scripts/provider_attempts.py`
- Modify: `tests/test_temporal_benchmark_contract.py`
- Modify: `tests/test_restraint_benchmark_contract.py`
- Modify: `scripts/run_reader.py`
- Modify: active callers found by `rg -l 'ProviderAttemptLedger\(|install_openai_meter\(' scripts benchmarks -g '*.py'`
- Modify: `tests/test_run_reader_contract.py`

**Interfaces:**
- Consumes: existing append-only hash-chained JSONL journal and file lock.
- Produces: `ProviderAttemptLedger(..., authorization_sha256, screen_id, hard_ceiling_nanos, opening_liability_nanos)`, `assert_open()`, `record("start", ..., {"max_liability_nanos": ...})`, `record_reconciliation(...)`, `close_campaign(...)`, and an authoritative nano-USD snapshot.

- [ ] **Step 1: Write failing ledger tests**

Add focused tests that create two sequential screen instances over the same path and assert:

```python
first = ProviderAttemptLedger(path, auth, "screen-a", 200_000, 10_000)
first.record("start", "a", {**start, "max_liability_nanos": 100_000})
first.record("result", "a", {"response": paid_response(cost=0.00005)})
first.close()
second = ProviderAttemptLedger(path, auth, "screen-b", 200_000, 10_000)
with pytest.raises(RuntimeError, match="campaign hard ceiling"):
    second.record("start", "b", {**start, "max_liability_nanos": 150_001})
```

Also cover interrupted/error reservation persistence, upward decimal-to-nano rounding, receipt-bound reconciliation, terminal journal closure surviving absent tombstone projection, a valid cache-path `assert_open()` at the exact ceiling, and `install_openai_meter` rejecting a missing `max_liability_nanos` before calling the fake SDK.

- [ ] **Step 2: Run the tests and verify red**

Run:

```bash
python3 -m pytest tests/test_temporal_benchmark_contract.py tests/test_restraint_benchmark_contract.py -q
```

Expected: failures identify the absent schema-v2 campaign fields, ceiling enforcement, reconciliation/close transitions, and metered reservation.

- [ ] **Step 3: Implement the minimum journal state machine**

Use schema `2` with header fields `authorization_sha256`, `hard_ceiling_nanos`, and `opening_liability_nanos`. Replay accepts `start`, `result`, `error`, `reconcile`, and terminal `closed`; it rejects any event after `closed`. Compute authoritative liability as:

```python
opening_liability_nanos + settled_nanos + unresolved_max_liability_nanos
```

Convert provider decimal strings through `Decimal(str(value))` and `ROUND_CEILING`. Under the campaign file lock, validate auth/path/open state, allow the caller to validate a same-packet cache, and on a cache miss append+fsync `start` before credentials/provider. Bind `screen_id` on each event. A `result` substitutes rounded authoritative cost; `error`, interruption, or unpriced output retains the full reservation. `reconcile` names the old reservation and receipt/proof SHA-256; `closed` is the authoritative terminal journal event and the JSON closure is only an atomic projection.

- [ ] **Step 4: Migrate all paid callers**

Every active direct ledger and `install_openai_meter` caller supplies one authorization scope, canonical journal path, screen ID, hard ceiling, opening liability, and per-attempt worst-case reservation. `install_openai_meter` accepts an already-open campaign ledger rather than minting an independent journal. In `ReaderCli.call`, perform authorization/path/closed preflight, validate a same-packet cache, and reserve only a miss before constructing or accessing the provider. Bind cache entries to authorization scope plus original attempt/result hashes. Remove screen-specific execution data from the journal fingerprint; keep it in event payloads. Do not retain an old constructor path that lacks a hard ceiling.

- [ ] **Step 5: Run focused and full Python contracts**

Run:

```bash
python3 -m pytest tests/test_temporal_benchmark_contract.py tests/test_restraint_benchmark_contract.py tests/test_memora_benchmark_contract.py tests/test_run_reader_contract.py -q
python3 -m pytest tests/ -q
```

Expected: all pass and no test can start an unreserved paid attempt.

- [ ] **Step 6: Commit**

```bash
git add scripts/provider_attempts.py scripts benchmarks tests
git commit -m "feat: enforce cumulative paid campaign ceiling"
```

### Task 2: Source-Neutral Structured Observation Contract

**Files:**
- Modify: `crates/memphant-core/src/structured_state.rs`
- Modify: `crates/memphant-core/src/lib.rs`
- Modify: `crates/memphant-core/tests/structured_state_projection.rs`

**Interfaces:**
- Consumes: `ActiveStructuredState`, `StructuredStateOp`, `ProjectedStructuredState`, existing canonicalization and interval validators.
- Produces: `StructuredSourceKind`, `EvidenceSlice`, `StructuredObservationDisposition`, `StructuredObservation`, generalized `StructuredStateRequest`, `evidence_slices_for_episode`, `evidence_slices_for_resource`, `fold_structured_observations`, and generalized `project_structured_state`.

- [ ] **Step 1: Write failing pure-contract tests**

Create tests using these shapes:

```rust
let request = StructuredStateRequest {
    source_kind: StructuredSourceKind::Resource,
    source_body_sha256: sha256(body),
    batch_index: 0,
    evidence_slices: evidence_slices_for_resource(body, &[]).unwrap(),
};
assert_eq!(request.evidence_slices[0].source_span, "0-12");
assert!(!request.evidence_slices[0].id.contains(resource_id.as_uuid().to_string().as_str()));
```

Cover resource exact quote success, episode quote outside user/user-agent rejection, neutral-ID stability across local source IDs, slice substitution/span shift/quote mismatch/UTF-8 failure, short-resource full-body fallback, deterministic ordered fold, last-state-wins without stale target IDs, and append predicate identity from `(source_kind, local source ID, canonical span)`.

- [ ] **Step 2: Run the focused Rust test and verify red**

```bash
cargo test -p memphant-core --test structured_state_projection
```

Expected: compile failures for the new contract names.

- [ ] **Step 3: Implement evidence slices and observations**

Use these public shapes, with serde `deny_unknown_fields`:

```rust
pub enum StructuredSourceKind { Episode, Resource }
pub struct EvidenceSlice { pub id: String, pub body: String, pub source_span: String }
pub enum StructuredObservationDisposition { State, Event }
pub struct StructuredObservation {
    pub namespace: String,
    pub item_key: String,
    pub fields: BTreeMap<String, Value>,
    pub disposition: StructuredObservationDisposition,
    pub evidence_slice_id: String,
    pub evidence_quote: String,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
}
pub struct StructuredStateRequest {
    pub source_kind: StructuredSourceKind,
    pub source_body_sha256: String,
    pub batch_index: usize,
    pub evidence_slices: Vec<EvidenceSlice>,
}
```

Neutral slice IDs hash only source kind, canonical span, and slice-body hash. Provider observations have no operation or target IDs. Resolve quotes inside their named slice, translate to exact parent span, validate the original source body, then fold once against freshly supplied `ActiveStructuredState`. Preserve existing quantity-event validation and canonical projected bodies.

- [ ] **Step 4: Export the contract and run tests**

```bash
cargo fmt --all
cargo test -p memphant-core --test structured_state_projection
cargo test -p memphant-core --lib structured_state
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add crates/memphant-core/src/structured_state.rs crates/memphant-core/src/lib.rs crates/memphant-core/tests/structured_state_projection.rs
git commit -m "feat: add source-neutral structured observations"
```

### Task 3: Bounded Provider Request and Receipt Identity

**Files:**
- Modify: `crates/memphant-runtime/src/structured_state_openrouter.rs`
- Modify: `crates/memphant-runtime/src/lib.rs`
- Modify: `config/structured-state-v1.txt`

**Interfaces:**
- Consumes: Task 2 `StructuredStateRequest` and `StructuredObservation`.
- Produces: strict evidence-slice observation schema, one pure `plan_structured_state_request` used by dispatch and census, exact serialized request guard/reservation, source-neutral `extraction_key`, and attempt events bound to source kind/body hash/batch/key.

- [ ] **Step 1: Write failing inline provider tests**

Add/replace tests around `request_uses_strict_supported_openrouter_parameters` and `oversized_request_is_rejected_before_transport` to assert:

```rust
assert!(value["messages"][1]["content"].as_str().unwrap().contains("evidence_slices"));
assert!(value["messages"][1]["content"].as_str().unwrap().contains("source_body_sha256"));
assert!(value["messages"][1]["content"].as_str().unwrap().find("resource_id").is_none());
```

Cover strict decoding of `evidence_slice_id`, unknown slice rejection, request size including prompt/schema/escaping, exact 128-KiB rejection before transport, deterministic extraction-key drift for prompt/schema/model/batching changes, requested identity in lookup key, served identity in receipt, retry-multiplied nano-USD reservation, dispatch bytes/hash exactly equal to census plan bytes/hash, no secret/transport access from the planner, and attempt events containing source kind/body hash/batch/key rather than episode UUID.

- [ ] **Step 2: Run the runtime tests and verify red**

```bash
cargo test -p memphant-runtime structured_state_openrouter
```

Expected: compile/schema assertion failures against the episode-operation protocol.

- [ ] **Step 3: Implement the strict observation protocol**

Build provider JSON only from `source_kind`, `source_body_sha256`, `batch_index`, and neutral slices. Decode `Vec<StructuredObservation>`; trusted core validation resolves spans later. Hash contract revision, source kind/body hash, ordered neutral slice IDs/spans/body hashes, requested model/provider policy, prompt/schema hashes, request parameters, and batching parameters into `extraction_key`. `plan_structured_state_request` returns canonical serialized bytes, request SHA-256, extraction key, per-attempt nano-USD reservation, and attempt maximum without reading environment variables or opening a transport. Dispatch consumes that exact plan. Keep `MAX_REQUEST_BYTES = 131_072` over the final serialized JSON. Update the prompt to request state/event observations and forbid target IDs, operations, tenant identity, and inferred evidence.

- [ ] **Step 4: Run focused runtime verification**

```bash
cargo fmt --all
cargo test -p memphant-runtime structured_state_openrouter
cargo clippy -p memphant-runtime --all-targets --all-features -- -D warnings
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add crates/memphant-runtime/src/structured_state_openrouter.rs crates/memphant-runtime/src/lib.rs config/structured-state-v1.txt
git commit -m "feat: extract bounded structured observations"
```

### Task 4: Resource Reflection, Deterministic Fold, and Prepared Receipts

**Files:**
- Modify: `crates/memphant-core/src/service.rs`
- Modify: `crates/memphant-core/src/lib.rs`
- Modify: `crates/memphant-runtime/src/lib.rs`
- Modify: `crates/memphant-store-postgres/src/store.rs`
- Modify: `crates/memphant-core/tests/resource_contextual_chunk_write.rs`
- Modify: `crates/memphant-core/tests/reflect_claim_regressions.rs`
- Modify: `crates/memphant-core/tests/worker_queue_contract.rs`
- Modify: `crates/memphant-store-postgres/tests/pg_store_contract.rs`

**Interfaces:**
- Consumes: Tasks 2-3 source-neutral requests/observations and the existing resource contextual chunks, mutation path, and store job result.
- Produces: `ReflectResource` structured compilation, deterministic batching/fold, fresh tenant-local mutations, and prepared-result input/receipt bindings.

- [ ] **Step 1: Write failing service and store tests**

Add tests proving that a retained Resource produces a raw Resource unit plus compiled Semantic/Procedural units whose `source_resource_id` is set and `source_episode_id` is absent. Cover short-body fallback, oversized slice lossless splitting, multi-batch equality to one-batch folding, compiler identity drift requeue, reordered observation manifest rejection, prepared-result hash tamper rejection, two-tenant replay producing distinct local unit/citation IDs, and existing forget/no-resurrection behavior.

- [ ] **Step 2: Run focused tests and verify red**

```bash
cargo test -p memphant-core --test resource_contextual_chunk_write --test reflect_claim_regressions --test worker_queue_contract
cargo test -p memphant-store-postgres --test pg_store_contract prepared_structured
```

Expected: failures show that `ReflectResource` returns no structured projections and prepared state lacks receipt bindings.

- [ ] **Step 3: Wire resource compilation**

Apply `structured_compiler_identity` to both Episode and Resource retains. In `prepare_structured_state`, fetch the correct source body, produce deterministic slices, pack complete serialized requests within 128 KiB, invoke bounded batches, validate all observations, fetch active state once immediately before folding, and project once. Append projections after the raw Resource candidate without changing `ReflectInput.resource_id` or citation minting.

- [ ] **Step 4: Bind prepared results**

Extend `ReflectJobResult::Prepared` with:

```rust
input_manifest_sha256: String,
extraction_receipt_sha256s: Vec<String>,
projections: Vec<ProjectedStructuredState>,
```

Validate both hashes on fetch and retry in the in-memory, runtime-delegated, and PostgreSQL stores. Cache only validated source-neutral observations in the sealed benchmark artifact; tenant-local projections are always fresh.

- [ ] **Step 5: Run focused and workspace Rust checks**

```bash
cargo fmt --all
cargo test -p memphant-core --test resource_contextual_chunk_write --test reflect_claim_regressions --test worker_queue_contract
cargo test -p memphant-store-postgres --test pg_store_contract prepared_structured
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets --all-features
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crates/memphant-core crates/memphant-runtime/src/lib.rs crates/memphant-store-postgres
git commit -m "feat: compile structured state from resources"
```

### Task 5: Official-Profile Census and Statistical Proof Contract

**Files:**
- Modify: `benchmarks/longmemeval_v2/memphant_memory.py`
- Modify: `benchmarks/manifests/longmemeval_v2.lock.json`
- Modify: `tests/test_longmemeval_v2_packing_adapter.py`
- Modify: `crates/memphant-cli/src/main.rs`
- Create: `crates/memphant-cli/src/structured_state_census.rs`
- Create: `benchmarks/manifests/longmemeval_v2.state_aware_full.v1.json`
- Create: `scripts/run_lme_v2_state_aware.py`
- Create: `tests/test_run_lme_v2_state_aware.py`

**Interfaces:**
- Consumes: Task 1 campaign ledger and Task 4 resource construction proof.
- Produces: production-planner-backed CLI census, a query-blind no-model `CAMPAIGN-CENSUS.json`, construction-proof schema v2, exact admission decision, sealed-prefix protocol, exact paired statistics, and official-package claim validator. The historical `scripts/run_lme_v2_p1_t6.py` and its manifest remain immutable evidence.

- [ ] **Step 1: Write failing census and proof tests**

Use a tiny fixture with repeated source bodies to assert content-addressed deduplication, exact request-byte/token/reservation maxima, official `memory_context_max_tokens = 200000`, and:

```python
assert census["admission"]["formula"] == "4258002400+C+451*(2*R+S)+10000000000<=200000000000"
assert census["admission"]["authorized"] is False
```

until explicit exact values satisfy the formula. Cover construction-proof v2 tampering for auth/campaign/screen, selection/input, state mode, model/provider, prompt/schema/code, cache provenance, attempt IDs, journal hashes, settled/unresolved totals, and target-answer field access. Add exact McNemar fixtures including `W=2,L=0 -> p=0.25`, `W=5,L=0 -> p=0.03125`, missing-pair rejection, 5-point effect gate, premise-regression rejection, and tie-not-SOTA.

- [ ] **Step 2: Run Python tests and verify red**

```bash
python3 -m pytest tests/test_run_lme_v2_state_aware.py tests/test_longmemeval_v2_packing_adapter.py -q
```

Expected: failures identify the absent census, proof-v2 fields, official profile, and exact paired validator.

- [ ] **Step 3: Implement the no-model census**

Expose `memphant structured-state census --input-jsonl ...` from the CLI. It streams resources through the same production slice packer and Task 3 request planner, deduplicates exact extraction keys, and emits hash-bound counts plus `C` without provider environment or network access. The Python orchestrator verifies pinned code/data, enumerates all official Medium trajectories with the adapter's exact fragmentation contract, and feeds only source bodies/metadata to the CLI; it never loads answer/oracle fields. Emit official 200K reader/judge worst-case `R`, candidate recall-wide `S`, carried opening liability, contingency, total nano-USD, and boolean admission. Refuse authorization when any identity/price/request maximum is missing or the inequality fails.

- [ ] **Step 4: Upgrade adapter and proof**

Enable Resource structured construction through the generic service contract. Construction proof v2 binds every identity and journal field named in the design; query-only reuse revalidates the sealed source-neutral artifact and performs fresh tenant-local writes/receipts. Pin the official leaderboard compute, combine, and build-submission scripts in `longmemeval_v2.lock.json`. The 12-case operational prefix writes encrypted/sealed answers and exposes only structural validity, receipt, settlement, and cumulative-liability predicates until the remaining 439 are committed.

- [ ] **Step 5: Implement exact paired validation**

Use Python standard-library integer binomial tails:

```python
p_value = sum(comb(D, k) for k in range(W, D + 1)) / (2 ** D)
effect = (W - L) / 451
```

Require all 451 native-judge pairs, `p <= 0.05`, an exact-compatible one-sided paired-risk-difference lower bound above zero, effect at least `0.05`, zero premise regressions, positive LAFS against the pinned upstream reference frontier, and full settlement. External SOTA remains false unless an accepted submission and frozen official leaderboard snapshot prove a strict win over every entry.

- [ ] **Step 6: Run focused tests and the real no-model census**

```bash
python3 -m pytest tests/test_run_lme_v2_state_aware.py tests/test_longmemeval_v2_packing_adapter.py -q
cargo test -p memphant-cli structured_state_census
python3 scripts/run_lme_v2_state_aware.py census --manifest benchmarks/manifests/longmemeval_v2.state_aware_full.v1.json --output docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/CAMPAIGN-CENSUS.json
```

Expected: tests pass; the census emits either a validator-backed authorization-ready packet or a precise zero-spend rejection with the failing term.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/longmemeval_v2 benchmarks/manifests/longmemeval_v2.lock.json benchmarks/manifests/longmemeval_v2.state_aware_full.v1.json crates/memphant-cli scripts/run_lme_v2_state_aware.py tests docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/CAMPAIGN-CENSUS.json
git commit -m "feat: gate official state-memory campaign"
```

### Task 6: Full Verification, Authorization Decision, and Economical Execution

**Files:**
- Modify: `docs/superpowers/specs/memphant/STATUS.md`
- Create: `docs/build-log/2026-07-26-state-resource-compiler-proof.md`
- Create only if census passes: `docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/CAMPAIGN-AUTHORIZATION.json`
- Create only through the runner: campaign journal, construction proof, paired outputs, native metrics, closure, and submission package artifacts.

**Interfaces:**
- Consumes: Tasks 1-5 and the repository verification commands in `AGENTS.md`.
- Produces: complete secret-free verification, exact paid/no-paid decision, and either official positive evidence or an honest terminal rejection.

- [ ] **Step 1: Run the complete secret-free gate**

```bash
python3 -m pytest tests/ -q
python3 scripts/check_spec_drift.py
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets --all-features
cargo test --doc
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant MEMPHANT_TEST_DATABASE_URL cargo test -p memphant-store-postgres -p memphant-worker -- --ignored --test-threads=1
cargo run -p memphant-cli -- db lint --provider plain-postgres
cargo run -p memphant-cli -- db lint --provider supabase
cargo run -p memphant-cli -- db lint --provider neon
python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run
DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant bash scripts/e2e_probe.sh
```

Expected: every command passes. A known environment failure is recorded separately and cannot become a code pass.

- [ ] **Step 2: Validate the census and freeze execution identities**

Re-fetch official upstream metadata and prices from primary sources, record immutable revisions/hashes/licenses/models/providers, rerun the census, and validate it. If admission is false, write and commit the exact rejection; do not create an authorization packet or make a paid call.

- [ ] **Step 3: If admitted, execute the sealed operational prefix**

Wrap only the secret-consuming runner with:

```bash
doppler run --project syndai --config dev -- python3 scripts/run_lme_v2_state_aware.py run --authorization docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/CAMPAIGN-AUTHORIZATION.json --sealed-prefix 12
```

Continue only if the validator exposes zero provider/parser/receipt/settlement failures and no answers or scores. Any failure appends the terminal campaign close and produces a rejection.

- [ ] **Step 4: If the prefix passes, execute and validate the remaining census**

Resume the same canonical journal/cache/output paths for all 451 questions. Run the official native judge once after all pairs complete, validate construction and attempt proofs, compute exact paired statistics and official metrics, build the upstream submission package, append the terminal close, and require cumulative settled plus unresolved liability `<= 200_000_000_000` nano-USD.

- [ ] **Step 5: Record exact claims and run final verification**

Update `STATUS.md` only for predicates backed by artifacts in the same change. State official benchmark success, statistical Deep-over-Fast success, positive LAFS, and external SOTA as separate predicates. Re-run the narrow validators plus `git diff --check`; commit the proof or rejection.

```bash
git add docs/superpowers/specs/memphant/STATUS.md docs/build-log docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot
git commit -m "docs: record state-resource compiler evidence"
```
