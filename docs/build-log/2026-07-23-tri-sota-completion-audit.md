# Tri-SOTA completion audit — 2026-07-23

Status: initial read-only reconciliation. This document classifies work; it is
not proof that an unchecked gate passed. Ledger changes require the named proof
artifact in the same logical checkpoint.

## Audited state

- Program worktree: `codex/memphant-tri-sota-completion` at
  `a21479e3f558fe83188bbf06c2144c11326d19b5`, clean when the audit began.
- Base and verified B3 head: `a21479e3f558fe83188bbf06c2144c11326d19b5`.
- Verified B2 head: `9c52b8e4bd3f75420fb64df99e64245ff716336b`.
- Syndai `main`, tracked `Syndai/main`, and fetched remote `main` were all
  `5e94b38a1987c9d8a2397fc1154ddf0c76e31737` and clean during the cutover audit.
- No paid call, secret use, database mutation, production access, push, PR,
  merge, or deployment occurred during the audit.
- The historical `run-65981e4f` root is absent under `/Users/sidsharma`. Its
  former bytes and immutability cannot be reverified. It must not be recreated
  or cited as preserved evidence.

Classification vocabulary: **complete** means named proof exists;
**proof-missing** means a mechanism exists but its exit predicate has not been
demonstrated; **unimplemented** means product or harness work remains;
**stale-text** means the plan no longer describes current code/evidence;
**rejected/deferred** means a recorded kill rule fired; **external-block** means
the missing input is outside these repositories or requires authorization.

## Phase 0 and Phase A

| Item | Classification | Evidence and terminal action |
|---|---|---|
| P0.1 run-owned Postgres | Complete for the existing campaign harnesses | Scratch/run-owned helpers and the P0.4 proof exist. Preserve this discipline for every new campaign. |
| P0.2 liveness preflight | Complete | Code-enforced preflight exists. Every new paid runner must reuse it. |
| P0.3 live Deep smoke | Complete with concerns | Named proof: `docs/build-log/2026-07-21-p0.3-live-deep-smoke.md`. It proves plumbing, not answer quality or promotion. |
| P0.4 chunk-span reliability | Complete | Named proof: `docs/build-log/2026-07-21-p0.4-chunk-span-resolved.md`; 670/670 packaged ingestion, zero calls. |
| A0 buried-evidence differential | Proof-missing in the real-binary E2E leg | The Rust mock differential exists. `e2e_probe.sh` still lacks the requested Deep leg; Deep remains diagnostic and no paid work depends on adding it now. |
| A1 Fast-miss classification | Complete; kill-switch fired | Named proof: `docs/build-log/2026-07-21-a1-fast-miss-classification.md`; 166/166 were present in pool, zero depth-bound misses. |
| Rung 7 packing diagnosis | Complete on retrieval only | `pack_render_cap=1200` improved Recall@10 by 0.2349 on the exposed 166-question split. The two seeds replayed the same deterministic full split; they are not independent replication. |
| Rung 7 reader/answer gate | External-block on paid authorization after free preparation | Build and freeze the paired gate. Do not execute models or flip the default without the separately requested authorization. |
| A2 n=12 Deep | Deferred by A1 | Do not reopen. Historical artifacts are not an authorization. |
| A3 Deep bench wiring | Deferred by A1 | No free spike or paid n=30 is justified without a new depth-bound bank and owner authorization. |
| A4 n~=100 Deep | Deferred by A1 | Same boundary as A2/A3. |

## Phase B

| Item | Classification | Evidence and terminal action |
|---|---|---|
| B1 observation block | Rejected and deleted | Named proof: `docs/build-log/2026-07-22-b1-observation-block.md`; 0/12 in both arms. Do not revive. |
| B2 file plane | Complete | Canonical projection plus compile/sync fixed-point proof at B2 head `9c52b8e4...`. |
| B3 distribution wedge | Complete locally | Named proof: `docs/build-log/2026-07-23-b3-distribution-wedge.md`; machine gate under `docs/build-log/artifacts/b3-distribution-wedge/`. Not pushed, landed, deployed, or CI-proven. |
| B4 verified receipts | Unimplemented; substrate exists | `StoredCitation` already owns source IDs, UTF-8 byte spans, and quote hash; recall drops these to ID-only citations. Add one tenant-bound verifier over canonical source bytes and persist receipts in the existing trace. No second evidence store or migration is justified. |
| B4 evidence status/policy | Unimplemented | Add a versioned MemPhant contract with `supported`, `contradicts-premise`, `near-match`, and `insufficient`, plus a deterministic server-owned answer policy. These requested names are not an official LongMemEval-V2 wire contract. |
| B5 WS-0 stubs/spikes/compare/schema comments | Complete | Landed in inherited history. `retention_tier` is live, not a dormant table. |
| B5 heuristic reranker | Complete at `2cd157a4` | Harmful stage, learned wrapper, request/trace/eval surface, and synthetic promotion fixtures deleted. Distinct cross-encoder seam preserved default-off. |
| B5 `Balanced` / decomposition | Rejected and deleted at `2cd157a4` | Real Postgres comparison was zero-delta at @5/@10. `Balanced` was removed coherently after its only distinct retrieval behavior was rejected; no alias remains. |
| B5 l4 names / `subject_hint` | Proof-missing debt audit | Remove only live redundant authority. Avoid cosmetic churn in immutable artifacts. |
| B5 stale strict-contract scripts | Complete at `2c2358d6` | One strict episode builder now serves StateBench, STALE, and Memora; external source kinds map explicitly and unknown values fail closed. `gate_run_memphant.py` was already current. |
| B5 Python repository gate | Complete locally at `2c2358d6` | 723 passed / 11 skipped / 0 failed. Frozen campaign evidence remains immutable; private corpus spans resolve against their pinned Git commit. Skips remain skips. |
| B6 CI honesty | Complete locally | Inherited CI legs and scratch-PG proof exist. Remote CI remains unproven until a separately authorized push. |

## Phase C and cutover

| Item | Classification | Evidence and terminal action |
|---|---|---|
| C0 strict public rails | Complete | Focused cross-repo adapter/binding/OpenAPI audit: 59 Syndai tests passed. Server-owned subject generation is already consumed; old plan text saying generation `0` is fabricated is stale. |
| C0 real identities | Partial | Stable Syndai user and agent IDs are used. Thread `Agent.parent_agent_id` as the parent node reference when present; do not invent attachment hierarchy. |
| C1 episodic correctness | Complete | Named proof: `docs/build-log/2026-07-22-c1-episodic-slice.md`; state filtering, tenancy, and packaged SLO are proven. |
| C1 live episodic read | Unimplemented | Syndai L0 episodic memory still reads Syndai storage. Correctness does not prove cutover or recall-quality parity. |
| C1 quality parity | External/evidence block | No accepted live parity corpus/oracle is authorized. Keep separate from mapping correctness and UI equivalence. |
| C2 docs | Rejected by free kill-gate | Named proof: `docs/build-log/2026-07-22-c2-docs-slice-killgate.md`; do not revive. |
| C3 strict ingest mechanism | Complete | Focused extract/mine/runner contracts: 79 tests passed. Current runner uses binding plus nested `payload.episode`. |
| C3 public trajectory adapter | Unimplemented | Pin `nebius/SWE-rebench-openhands-trajectories` revision `35455389ab51bf5e2306bfd436ef72d0f98bf882` (CC-BY-4.0), sample whole trajectories deterministically, record every transformation/drop, and fail on unknown roles. |
| C3 realistic-volume proof | Unimplemented | Run approximately 1,000 trajectories / approximately 64k turns through packaged MemPhant and scratch Postgres in Fast mode; prove provenance, throughput, two-tenant isolation, no silent drops, bounded failures, and adversarial continuity. Label it public synthetic model-generated traffic, never organic production traffic. |
| C3 adversarial reader | External-block on paid authorization | Mining may be free/deterministic, but the existing generator/reader is paid. Prepare one consolidated authorization packet; continue all free work. |
| WS-F active dogfood read | Implemented seam, proof missing | Use the existing L1+ loader seam. It is default-off and current mission consumers swallow hard contract errors, so the dogfood predicate remains open. |
| Loud dogfood failures | Unimplemented root fix | Propagate non-transient contract/auth failures through both mission consumers; degrade only transport faults. Add a real-loader scratch-PG proof with two API keys and a malformed contract. |
| Private mirror drift | Unimplemented repair | `check_spec_drift.py` currently reports `STATUS.md` and `08-api-sdk-mcp-spec.md` drift. Update mirrors only with the owning evidence and preserve unrelated Syndai work. |

## Phase D

| Item | Classification | Evidence and terminal action |
|---|---|---|
| D1 LongMemEval-V2 dual point | Deferred by A1 | No new Deep campaign or leaderboard claim without a new frozen depth-bound bank and explicit authorization. |
| D2 ForgetEval | Unimplemented adapter; current upstream runnable | Pin official upstream `b6053b7...`; map reset/inscribe/recall/supersede/release exactly. Selective purge is unsupported and must report N/A; subject erasure is not a substitute. Mutation correctness is the prerequisite. |
| D3 LME-S full-500 | Deferred by A1 | Do not run or use SOTA language. |
| D4 SWE-Explore | External-block reconfirmed | Current public package still omits required issue text/base commits; retain fail-closed evidence. |
| D4 SWE Context Bench | Official harness now public; no-model smoke complete; model run unauthorized | Pinned code `31bb04155f52b184bf31b220e3cff0607ac9c953` and dataset `5bec275a2095768a53ac804ae4fdf90b1723b8af`; official input combiner and evaluator import smoke passed on one Lite case. No Docker task, model, MemPhant adapter, or benchmark score was run. The frozen gate remains awaiting exact model/compute budgets and explicit authorization. |
| Supporting Memora/STALE/MemSyco/MemBench | Supporting-only | Repair public contracts when required, but never promote them to headline evidence. |

## Reopened STATUS predicates

| Predicate | Classification | Required terminal state |
|---|---|---|
| WS-F / Dogfood | Proof-missing | Active public-contract read, tenant-bound trace, loud contract failure, transport-only degradation. |
| WS-G | Proof-missing | Surface exists; real promotion/public evidence is missing. No launch flip in this program without that evidence. |
| Rung 5 temporal validity | Proof-missing | Needs paired stale/current real-runtime evidence. |
| Rung 6 edge expansion | Rejected on current corpus, implementable only with justified edge-minting corpus | Existing real result is zero delta. Do not leave it described as a win. |
| Rung 7 packing+abstention | Retrieval proven; reader proof external-blocked on authorization | Keep default off and rung open until paired reader evidence. |
| Rung 8 bounded cross-rerank | Partial | Preserve seam default-off; restraint, holdout, latency, and cross-lane non-regression remain open. |
| Rung 9 decomposition | Rejection decision required | Existing real result is zero delta. Record rejection/delete before removing `Balanced`, unless a justified real composite bank exists. |
| Rung 10 procedural | Proof-missing | Type exists; task-success/outcome-writeback evidence does not. |
| Rung 11 decay | Proof-missing | Longitudinal evidence absent. |
| Rung 12 Deep | Deferred/diagnostic | A1 binds; no promotion. |
| Rung 13 learned rerank | Rejection/deletion candidate | Training floor absent. Delete dormant heuristic/learned machinery unless separately justified; preserve cross-encoder seam. |
| Rung 15 belief composition | Proof-missing | Requires real restraint and corroborated-promotion precision. |
| Public launch | Open | Local mechanisms and synthetic fixtures cannot close benchmark, restraint, WS-G, or cutover predicates. |
| Restraint launch | Open | Prior scorecard was synthetic and was correctly reopened. |
| GateMem conditional | Open | Prior scorecard was synthetic and was correctly reopened. |

## Frozen architecture and claim boundaries

1. Receipts extend the citation/trace authority already in the product. SHA-256
   over exact source bytes proves reproducibility; it is not a signature or a
   defense against a compromised database/operator.
2. Resource receipts fail closed for non-empty ACLs until ordinary recall has a
   real ACL authorization implementation. Protected/trust-ineligible resources
   cannot mint verified receipts.
3. The requested status quartet is MemPhant-owned and versioned. Current
   LongMemEval-V2 code uses related internal values
   (`directly_supported`, `contradicts_premise`, `near_match_only`,
   `insufficient`) and policy mappings, but the public benchmark adapter itself
   only requires returned context. No upstream-compatible schema claim is made.
4. Packing retrieval evidence is not reader-QA. C1 correctness is not recall
   parity. A healthy server is not dogfood. Public trajectory data is not
   production traffic. Local gates are not CI. No individual rung proves SOTA.

## Next implementation checkpoints

1. B4 receipt verifier, trace/public contract, deterministic evidence policy,
   tamper/replay/tenant/trust tests, regenerated artifacts, dated machine proof.
2. Packing reader-gate preregistration and all zero-call rehearsals; one
   consolidated paid authorization packet if execution remains necessary.
3. B5 decomposition verdict, heuristic deletion, strict script repair, and
   repository-gate repair.
4. C3 pinned public adapter plus packaged realistic-volume proof.
5. Clean Syndai worktree: loud active L1+ read, parent identity, two-tenant
   packaged proof, and mirror reconciliation.
6. D2 adapter/mutation proof and honest D4 official-harness smoke; final ledger
   reconciliation and independent review.
