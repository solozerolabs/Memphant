# Flow: outcome-coupled evolution for coding agents

## Spec

Prove that task outcomes can improve future memory packs before adding serving machinery. The primary outcome is `accepted_without_violation = validator_pass AND requested_end_state_pass AND zero_rule_violation`; retrieval quality and model explanations are not success metrics.

The experiment reuses the privacy-frozen local Track-U and Claude transcript corpus. Private transcript text and model bodies stay outside Git. Committed artifacts contain only schemas, hashes, counts, aggregate results, and public-safe methodology. Regex can nominate candidates but cannot label them.

Five policies share one evaluator: `C0` receives no learned memory, `C1` receives every static eligible memory, `A1` outcome-couples adherence units, `A2` all procedural units, and `A3` every objectively scoreable kind. Only `explicit_user`, `deterministic_scorer`, and `randomized_counterfactual` evidence changes ordering. Explicit silence excludes; the remaining order is positive causal evidence by Wilson helpful lower bound, unevidenced validated units, then statistically harmful units. Identical `C1` and treatment packs suppress model calls.

The first action look falsified ordering-only `A1`: all three arms scored 2/4. The approved follow-up tests a different, user-visible mechanism. A correction in run N validates one terse lesson; on run N+1 a deterministic task trigger admits that one relevant lesson into the agent's hot guidance. Long incident prose remains in `LEARNINGS.md`, which Syndai explicitly does not auto-load. The treatment is therefore `A4 = positive explicit outcome evidence AND deterministic trigger match`; it is compared against the already-settled `C1` full static pack without replaying controls. This is an adaptive mechanism screen, not confirmation evidence.

The execution gate is strict:

- Qualify up to four chronological correction-to-later-task cases per scope, with a learned-before-held-out rule, distinct later task, objective predicate, recoverable rule/context boundary, and no privacy exclusion.
- Reconstruct the actual post-compaction active context from `compact_boundary` records and validate its recorded token metadata.
- The six known long-session violations are liveness probes; a scope with fewer than four valid cases is `UNTESTABLE` and receives no spend.
- Reserve and settle every model call against the cumulative `$100` ceiling. Action look is capped at `$30`; isolated coding replay is capped at `$70`; ambiguous provider completion is never retried.
- Runtime tables, APIs, Claude spool, shadow dogfood, and prompt injection remain closed until a treatment has positive net wins, zero task/safety losses, and a live instrument.

If that gate opens, add only the approved append-only, tenant-bound `memphant.task_outcome` and `memphant.task_memory_event` records and their two POST endpoints. Preserve `mark`, `review_event`, and FSRS. Helpful/harmful lifecycle evidence rejects non-causal attribution, planned/actual file scope is normalized server-side, and raw prompts, commands, or transcript bodies are never accepted.

## Plan

1. Add one Python experiment module and one focused test module. The module reconstructs compaction boundaries, validates liveness metadata, computes Wilson ordering, suppresses identical packs, and emits a public-safe qualification artifact. Use stdlib only.
2. Preregister the free-gate result path in the evidence-contract registry before running the corpus. Store private case packets and any model bodies only under the existing private artifact root with checksums.
3. Run the `$0` qualification and lifecycle simulation. Record each scope as eligible, `UNTESTABLE`, or `no_policy_difference`; do not dispatch a model for ineligible/identical arms.
4. Only if an Axis arm passes the free gates, run the blinded same-context structured-action look with reserved cost and the exact pinned Claude model. Advance at most two policies under the stated 3/4, net-win, zero-loss rule.
5. Only if action look passes, run two isolated scratch-worktree coding cases, then expand to at most six only on positive net wins and zero losses. Never use production databases.
6. Only if isolated replay opens the runtime gate, implement the two records, two POST endpoints, crash-safe local JSONL spool, and silent-shadow policy computation with tenant/privacy/idempotency tests. Do not implement inversion, deletion, veto, semantic ingestion, or repo serving.
7. Add evidence contracts to every decisional artifact before reading results. Update STATUS only with its named passing proof. Run the full repository gate from AGENTS.md before claiming the workstream complete.
8. After the ordering-only screen is flat, preregister one admission follow-up. Reuse the four settled `C1` cells by checksum, dispatch only four `A4` relevant-lesson cells, and reserve at most `$10` of the unspent action-look budget. Advance only at 3/4, at least one net win, and zero losses. Do not reinterpret an unexposed historical memory as harmful causal evidence.
9. Because the admission screen began before its rules could act, run one final mechanism-sensitive isolated replay from a pre-action boundary. Prove repo-file and MemPhant-projection delivery parity without a model call, then compare `C0` against the projected single-lesson pack on two whole scratch-repo tasks. Grade filesystem state, deterministic validators, and the complete tool sequence. Reserve at most `$20`; expand no further unless the treatment has a net win and zero losses. This is an experiment adapter over the existing canonical projection, not production hook or outcome-schema work.

## Harness

```sh
python3 -m pytest tests/test_outcome_coupled_evolution.py -q
python3 scripts/check_evidence_contract.py
python3 scripts/instrument_power.py --check
python3 scripts/check_spec_drift.py
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
cargo test --doc
cargo run -p memphant-cli -- db lint --provider plain-postgres
cargo run -p memphant-cli -- db lint --provider supabase
cargo run -p memphant-cli -- db lint --provider neon
python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run
```

## Eng review (applied lens, autonomous — gstack-plan-eng-review)

- The user already selected the kill-gated scope. The minimum honest implementation is the reusable free instrument plus its decisional artifact; runtime code before causal evidence would violate the approved architecture.
- One module avoids three production policy implementations. Policy scope is data, causal eligibility is one shared predicate, and packing is one deterministic sort.
- The load-bearing failure mode is false qualification. Tests must prove chronological ordering, distinct tasks, objective grading, compaction metadata identity, liveness, negative controls, and spend suppression before any model output is inspected.
- Existing evidence-contract and scratch-Postgres machinery remain authoritative. No new decay engine, queue, service, or dependency is justified.

VERDICT: proceed with the free instrument. Conditional phases stay closed until their predicates pass. NO UNRESOLVED DECISIONS.

## Admission follow-up eng review (2026-08-08)

- What already exists: the four qualified contexts, structured-action scorer, blinded runner, budget ledger, and settled `C1` responses are reused. `LEARNINGS.md` remains the long-form source; the treatment models its evidence-gated projection into always-loaded guidance.
- Architecture: one new policy function selects the case's triggered unit. No database, exporter, generic retrieval, semantic matcher, or second decay system is added.
- Attribution correction: the source correction is `explicit_user` evidence that validates the lesson. A later violation cannot grade the lesson harmful unless exposure was recorded; the follow-up never makes that unsupported attribution.
- Tests: pin relevant-only admission, irrelevant-unit exclusion, reuse-by-checksum, four-cell budget reserve, no control redispatch, and the unchanged promotion rule.
- Failure modes: changed control body or context hash fails closed; missing trigger produces no dispatch; any loss closes the gate; provider ambiguity is not retried.
- Performance: four new calls maximum; private contexts remain local; public artifacts contain only hashes, IDs, counts, costs, and grades.
- Sequential implementation, no parallelization opportunity.
- NOT in scope: production runtime/API/spool, writing into Syndai, semantic matching, automatic prose generation, veto, deletion, and coding replay before this screen passes.

VERDICT: CLEAR. The follow-up tests admission rather than repacking, reuses banked controls, and has no unresolved decision.

### Admission follow-up result

`A4` again tied `C1` at 2/4 with zero wins and zero losses, spending `$5.0316035` (`$20.163992` cumulative). Post-result payload inspection found that the two failing replay points were not opportunities for the lesson to act: staging was preceded by a sensible repository inspection, and the scoped-gate replay began after the full gate was already running. The process verdict remains flat and runtime remains closed, but the mechanism conclusion is `UNRESOLVED_INSTRUMENT_NOT_SENSITIVE`, not rejected. No further model call is allowed under this follow-up; a future test must grade a short action sequence or an actual isolated task from a pre-action boundary.

## Isolated replay addendum review (2026-08-08)

- Reuse boundary: canonical projection units, causal attribution rules, budget ledger, pinned-model validation, and private response checksums remain authoritative. No runtime table, hook installer, retrieval service, or second store is added.
- Delivery test: the repo-file and projected lesson bodies must hash identically before dispatch. Since identical context cannot identify a storage effect, only `C0` and projected treatment receive model calls.
- Task test: two fresh scratch Git repositories begin from recorded clean commits. One grades explicit staging while preserving a preexisting dirty decoy; one grades scoped verification from before any test command. Both require the requested end state and deterministic validator success.
- Instrument: stream the full Claude tool sequence, capture before/after tree and index hashes, and reject fallback models, repeated dispatches, missing exposures, raw-content leakage, or a task boundary that starts after the relevant action.
- Stop rule: reserve at most four cells and `$20`. Flat or harmful results close the runtime gate; positive net wins with zero losses may open only the production-hook design gate.

VERDICT: CLEAR. This is the minimum test capable of observing the lesson at an actionable boundary; identical delivery is checked for free and paid work remains two paired tasks.

### Initial isolated replay result

The projected lesson arm passed 2/2 whole tasks versus 1/2 for no learned lesson, with one net win, zero losses, and `$0.3068625` new spend (`$20.4708545` cumulative). Both arms completed and validated both requested edits. The difference was the scoped-gate predicate: the no-memory arm ran both the focused test and the full repository gate, while the MemPhant arm ran only the focused test. Explicit staging was already correct in both arms. The preregistered verdict is `CODING_REPLAY_EXPAND`; the production runtime gate remains closed pending two fresh task variants.

## DX review (applied lens, autonomous — gstack-plan-devex-review)

- The first consumer is the benchmark operator, so a single command must produce a machine-readable reason for every skipped arm and never require reading private transcript bodies in committed output.
- `UNTESTABLE`, `no_policy_difference`, budget denial, and ambiguous provider completion are first-class outcomes rather than exceptions.
- Runtime API and hook onboarding are deliberately deferred; documenting endpoints that the evidence gate has not opened would create a false product contract.

VERDICT: the instrument is operable and failure-explicit; runtime DX remains gated. NO UNRESOLVED DECISIONS.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture and tests | 1 | CLEAR | Conditional runtime boundary preserved |
| DX Review | `/plan-devex-review` | Operator experience | 1 | CLEAR | Explicit no-spend outcomes required |

**VERDICT:** ENG + DX CLEARED — implement the free gate first

NO UNRESOLVED DECISIONS
