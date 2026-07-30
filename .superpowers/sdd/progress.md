# P1 Deep Recall SDD Progress

- Worktree: `/Users/sidsharma/.codex/worktrees/Memphant/p1-deep-mode`
- Branch: `codex/memphant-p1-deep-mode`
- Baseline: `f1a1c6d9`
- Plan commit: `f51ea43e`
- Full Python baseline: 534 passed, 12 skipped
- Full Rust all-target/all-feature baseline: passed
- Unrelated dirty file preserved: `docs/handoff/NEXT-SESSION-PROMPT.md`

## Task 1 - public `deep` contract

- Status: completed and approved
- Base: `f51ea43e`
- Commit: `0956c69c0be9e7328ccdc4e33f08dd102dd140f6`
- Brief: `.superpowers/sdd/briefs/p1-t6-task-1-public-deep-contract.md`
- Implementer: `/root/t6_task1_impl`
- Report: `.superpowers/sdd/p1-t6-task-1-report.md`
- Review package: `.superpowers/sdd/review-f51ea43e..0956c69c.diff`
- Reviewer: `/root/t6_task1_review`
- Fix commit: `4ef36af744141a42475d601623ef28aa14de3de5`
- Final review: approved after fresh Python/profile/serde checks

## Task 2 - readable fail-closed resource ACL

- Status: completed and approved
- Base: `4ef36af744141a42475d601623ef28aa14de3de5`
- Commit: `03bb70f8fd1f4324a4089bb548d34aeee735bf41`
- Brief: `.superpowers/sdd/briefs/p1-t6-task-2-resource-acl-read.md`
- Report: `.superpowers/sdd/p1-t6-task-2-report.md`
- Review package: `.superpowers/sdd/review-4ef36af7..03bb70f8.diff`
- Reviewer: `/root/t6_task2_review`
- Final review: approved after fresh type/InMemory/scratch-Postgres checks

## Task 3 - authorized canonical snapshot

- Status: completed and approved
- Base: `fc471a15`
- Commit: `15511564744fba3bc8465d97795f0787488caaca`
- Brief: `.superpowers/sdd/briefs/p1-t6-task-3-authorized-snapshot.md`
- Report: `.superpowers/sdd/p1-t6-task-3-report.md`
- Review package: `.superpowers/sdd/review-fc471a15..15511564.diff`
- Reviewer: `/root/t6_task3_review`
- Final review: approved with no P0-P2 findings after independent InMemory, scratch-Postgres, fmt/diff, and targeted all-feature clippy checks
- Contract follow-up plan commit: `b02b575d58c45ee8bfca4add67e23e15d722e93c`

## Task 4 - injectable bounded provider

- Status: completed and approved
- Base: `b02b575d58c45ee8bfca4add67e23e15d722e93c`
- Brief: `.superpowers/sdd/briefs/p1-t6-task-4-injectable-bounded-provider.md`
- Implementation commit: `8575f8e192925d8d8761261f5a6e24289d5aa31c`
- Fix commit: `ccd7cc24533ec06ce2a5ada928f5431fb314d5d0`
- Reports: `.superpowers/sdd/p1-t6-task-4-report.md`, `.superpowers/sdd/p1-t6-task-4-fix-report.md`
- Review package: `.superpowers/sdd/review-0dbf66f4..ccd7cc24.diff` (SHA-256 `a3ded58d5bcc4bf1b7067f04c0a5a8ef566e6eca08bc1ee0daad781abe260b54`)
- Reviewer: `/root/t6_task4_review`
- Final review: approved with no P0-P2 findings after independent security-order, evaluator-arm, provenance, latency, wire/schema/adapter, focused suite, fmt, clippy, and diff checks
- Runtime operating-plan commits: `0dbf66f4`, `f2f9d772`
- Task 5 activation prerequisite: inject the runtime provider and update the intentionally ignored remote rung's stale control assertion to typed-unavailable/no-trace before unignoring it

## Task 5 - real async file agent

- Status: completed and approved
- Base: `f2f9d772`
- Brief: `.superpowers/sdd/briefs/p1-t6-task-5-real-async-file-agent.md`
- Implementation commit: `f5e90dc0e9d93d73d7af9099bc849e4eaba957f1`
- Settlement/egress fix commit: `7e7a497b46dc7fd23b5b4aeb6cfd30ef3018fdb2`
- Proxy-test isolation commit: `560fb79f2f7e9f22725cf8d230e9354b37255074`
- Reports: `.superpowers/sdd/p1-t6-task-5-report.md`, `.superpowers/sdd/p1-t6-task-5-fix-report.md`
- Review packages: `.superpowers/sdd/review-f2f9d772..f5e90dc0.diff` (SHA-256 `12ee1477cdce32ba93e269e40d91da6b1a3e97825a05b87ae53fd45072073889`), `.superpowers/sdd/review-f5e90dc0..7e7a497b.diff` (SHA-256 `35d42249a2e51354ce30b674affe36a3e916fc6a66416c54214f9b7d3b33394b`)
- Reviewer: `/root/t6_task5_review`
- Final review: approved with no P0-P2 findings after independent settlement, paid-POST replay, redirect/proxy egress, generation binding, accounting, public-surface, targeted proxy, fmt, and diff checks
- Full packaged gate: Python 535 passed/12 skipped; spec drift clean; fmt/clippy clean; all-target/all-feature and doc tests clean; all three provider lints clean; migration dry-run clean; isolated live-Postgres contracts (67) and worker smokes (2) clean; real-binary Postgres e2e probe clean
- Paid model calls: none

## Task 6 - exposed n=12 feasibility gate

- Status: implementation and execution fixes approved; fresh 48-row root authorized, no benchmark row yet eligible for aggregation
- Brief: `.superpowers/sdd/briefs/p1-t6-task-6-exposed-n12-gate.md`
- Base: `560fb79f2f7e9f22725cf8d230e9354b37255074`
- Implementation commit: `cb322595e58f56f36f43c0204e30c9da600fae9b`
- Report: `.superpowers/sdd/p1-t6-task-6-report.md`
- Review package: `.superpowers/sdd/review-560fb79f..cb322595.diff` (SHA-256 `26db3d5391d33ebaff73ff7b75c3563c9f5e1e6d16353271ae69dbe75301c7c2`)
- No-credential preflight: pinned acquisition, 12-case/48-row manifest, materialization SHA-256, retain size limits, and 12 pairing proofs verified
- Execution-fix commits: `1f5b57cf` (context/chunk/transport), `05e2bf66` (audited route probe), `6e55c80f` (settlement-reservation enforcement), `354b2c3d` (prior-liability hard cap)
- Invalid execution evidence: `docs/build-log/artifacts/p1-t6/run-ee1575a6/INVALIDATION-PROOF.json`; zero eligible benchmark scores, never replay
- Release context authorization: runtime 32,757 tokens; official Qwen 23,564/32,768; non-empty/untruncated; 670/670 sources; zero paid calls
- Reader route authorization: one DeepInfra dispatch; HTTP 200 in 103.788 s; receipt settled on poll 6; 12 micro-dollars actual / 19 reserved; no replay
- Cumulative hard-cap proof: 14,995,200 fresh + 3,912 prior = 14,999,112 micro-dollars <= $15
- Report: `.superpowers/sdd/p1-t6-task-6-fix-report.md`
- Observed external dispatches during diagnosis: one original reader dispatch remains unresolved; one exact diagnostic and one tiny route probe settled. No completed benchmark row is eligible for aggregation.

## Corrected P1-T6 Task 1 - efficient paired execution contract

- Status: completed and approved
- Plan: `docs/superpowers/plans/2026-07-20-p1-t6-build-once-paired-gate.md`
- Base: `ef83becb`
- Implementation commit: `18405c52968e4dff3907cd89d0d6458ab1a85d5e`
- Aggregate-fix commit: `aeb3d280bbffbb6184e80955afc7ae556c91a7d0`
- Brief: `.superpowers/sdd/task-1-brief.md`
- Report: `.superpowers/sdd/task-1-report.md`
- Review package: `.superpowers/sdd/review-ef83becb..aeb3d280.diff`
- Reviewer: `/root/t6_efficient_task1_review`
- Final review: approved after the selected-arm aggregate fix; focused suite 48 passed, no skipped coverage
- Paid model calls: none

## Corrected P1-T6 Task 2 - frozen construction and query-only adapter

- Status: completed and approved
- Base: `aeb3d280bbffbb6184e80955afc7ae556c91a7d0`
- Implementation commit: `b640ebd0207773013608813498ad382b49f827ec`
- Brief: `.superpowers/sdd/task-2-brief.md`
- Report: `.superpowers/sdd/task-2-report.md`
- Review package: `.superpowers/sdd/review-aeb3d280..b640ebd0.diff`
- Reviewer: `/root/t6_efficient_task2_review`
- Final review: approved with no P0-P2 findings; 15 passed, 1 intentionally skipped packaged integration
- Paid model calls: none

## Corrected P1-T6 Task 3 - crash-safe case banks and paired clones

- Status: completed and approved
- Base: `ab6cda04`
- Implementation commit: `9e315792c8f52d3b55392d22cc4a89f209f78775`
- Hardening commits: `5d81561a953494a6bc5cea5a0f351b6d221008c4`, `0c67c13ad50177973ca62fc7fda7da88391c6949`
- Plan-hardening commit: `b95c46fd`
- Brief: `.superpowers/sdd/task-3-brief.md`
- Report: `.superpowers/sdd/task-3-report.md`
- Review package: `.superpowers/sdd/review-ab6cda04..0c67c13a.diff`
- Reviewer: `/root/t6_efficient_task3_review`
- Final review: approved with no P0-P2 findings; 74 passed, 1 deferred live integration skip
- Live read-only tool preflight: matching PostgreSQL 17.10 dump/restore selected before construction; default 14.23 rejected
- Paid model calls: none

## Corrected P1-T6 Task 4 - build-once aggregate evidence

- Status: completed and approved
- Base: `0c67c13ad50177973ca62fc7fda7da88391c6949`
- Brief: `.superpowers/sdd/task-4-brief.md`
- Report: `.superpowers/sdd/task-4-report.md`
- Implementation commit: `c1d6cf0591f13df739114f0ef3248c20c7964df5`
- Hardening commits: `25a70807e3256777bc738843cbf7909db36f42bd`, `4cceafaf4d3a30de564e536e01f6ce6c338c34bb`
- Review package: `.superpowers/sdd/review-0c67c13a..4cceafaf.diff`
- Reviewer: `/root/t6_efficient_task4_review`
- Final review: approved with no P0-P2 findings; 78 focused tests and 15 adapter tests passed, 1 deferred live integration skip
- Contract: exactly 12 Fast/Sonnet pairs, 12 unique sealed construction proofs, and 24 distinct case/arm clone identities
- Construction duration/cost is reported separately from Fast/Deep query recall and generation latency/cost
- Stopped diagnostic root `run-408363c9` remains immutable and ineligible
- T6 status: open until all n=12 evidence and aggregate predicates pass
- Paid model/database calls: none

## Corrected P1-T6 Task 6 Step 1 - efficient just-in-time preflight

- Status: completed and approved; paid execution remains credential-gated
- Pre-dispatch checkpoint commit: `e9d117308a7c3e01e5e30bc6a802ee8f8fbe7641`
- Context-reuse authorization commit: `7f935ad1b4e72599a63f6b5bf85936b5e854c463`
- Brief: `.superpowers/sdd/task-6-brief.md`
- Reports: `.superpowers/sdd/task-6-preflight-report.md`, `.superpowers/sdd/task-6-preflight-fix-report.md`
- Review package: `.superpowers/sdd/review-e9d11730..7f935ad1.diff`
- Reviewer: `/root/t6_n12_preflight_review`
- Final review: approved; static 23,564/32,768-token context proof reused because current exact Fast/Sonnet query and context hashes, adapter, relevant controller AST, and server/worker/CLI identities match
- Live-run rule: construct all 12 case banks normally in the fresh output root; no cross-root import or dependency on the ignored exact dump
- No-paid JIT preflight: frozen 12/24/12 bounds, 1.2 GB pinned acquisition, 12 materialized cases/pairing proofs, official harness environment, PostgreSQL 17.10 tools, release binaries, disk, and current endpoint inventory verified
- Remaining prerequisite: `OPENROUTER_API_KEY` is absent; `OPENAI_API_KEY` is present. No provider/model/database call was made

## Accuracy-first Phase 1b - substrate-transfer replay instrumentation

- Status: instrument landed and proven end-to-end; authoritative replay pending the Track R golden bank
- Worktree/branch: `/Users/sidsharma/Memphant-af-p1b`, `af-p1b`
- Base: `bf2c87c3`
- Implementation commit: `a2838bf1` (`scripts/code_lane_run_memphant.py`, `tests/test_code_lane_run_memphant.py`)
- Mechanism 1: every recall reads `GET /v1/traces/{id}` and records the trace's `dropped_items`/`RecallDropReason` per question (`pool_size`, `packed_size`, `gold_in_pool`, `gold_fused_rank`, `gold_fused_score`, `gold_drop_reason`, `drop_reasons`, `bucket`) plus a run-level `pack_drop_summary` (bucket split, `in_pool_unpacked_gold_drop_reasons`, `budget_share_of_in_pool_unpacked`). Mirrors `memphant-eval::bench_lme::classify_gold_drop_cause`; contract fields only, none invented
- Mechanism 2: `--pack-render-cap` admits `MEMPHANT_PACK_RENDER_CAP` for the server arm. `gate_runtime.Server` still closes inherited packing env vars, so the cap can only enter by explicit selection
- Gold identity on this lane is span-based (no session key), so gold-bearing pool units are resolved by reading episodic unit bodies from the per-run scratch DB and matching with `gate_common.contains_gold`
- Runnable check: `python3 -m pytest tests/test_code_lane_run_memphant.py -q` -> 26 passed. Four new cases cover the drop-reason plumbing, the hit/in-pool-unpacked/absent-from-pool split, the trace read through the bound context, and cap admission-not-inheritance (an ambient `MEMPHANT_PACK_RENDER_CAP=9999` must not reach a cap-OFF arm)
- Spend: $0. Deterministic retrieval-side only; no reader, no judge, no paid model call on any code path touched
- Instrument-proving runs (SYNTHETIC corpus, NOT evidence; scratchpad only, never committed): 4 attempts x 12 multi-line events, `--embed-model off --mode fast --k 10`, per-run scratch DBs via `with_scratch_db.sh`, release binaries built in this worktree
  - `--budget-tokens 512`: 6/6 questions `in_pool_unpacked`, `budget_share_of_in_pool_unpacked` 1.0 - the drop-reason plumbing produces populated per-question reasons
  - `--budget-tokens 8192`, cap OFF vs cap 1200: packed items per question 5 -> 7 with smaller per-item renders (q0 item chars `[3611, 7211, 3583, 3592, 3592]` -> `[3288, 3341, 3261, 3269, 3269, 3310, 3270]`), R@10 1.0 in both arms. The cap flag demonstrably reaches the packer
- Corpus-shape caveat found while proving the instrument: `episode_contextual_chunks` mints no chunks for a single-segment body, and `packed_render` only compacts chunk-rendered items - so on single-line event bodies the render cap is inert by construction (measured: byte-identical arms). The authoritative replay's cap arm is only interpretable on multi-line event bodies, which real OpenHands trajectory events have. Record the packed-item delta per arm before reading any retrieval delta
- Authoritative replay, one command per arm once a Track R bank exists (paths are explicit inputs; the golden lock next to `--golden` pins the corpus):
  `python3 scripts/code_lane_run_memphant.py --corpus <bank>/corpus.jsonl --golden <bank>/goldens.jsonl --out-evidence <out>/evidence-cap1200.jsonl --out-provenance <out>/provenance-cap1200.json --embed-model off --mode fast --k 10 --budget-tokens 8192 --label p1b-cap1200 --port 39466 --pack-render-cap 1200 --server-bin target/release/memphant-server --worker-bin target/release/memphant-worker --cli-bin target/release/memphant-cli`
  (drop `--pack-render-cap 1200` and change label/port/outputs for the cap-OFF arm)
- Not pushed
## Phase 1a-U — Track U user-learning golden bank, first slice

- Status: completed. $0 paid spend; no network, provider or database call.
- Privacy preregistration (committed BEFORE extraction): `docs/build-log/2026-07-30-track-u-privacy-prereg.md` (`10ea21aa`)
- Extractor: `scripts/user_lane_extract.py`; tests: `tests/test_user_lane_extract.py` (26 passed)
- Committed lock: `benchmarks/data/user_lane_golden.lock.json` (`b72d2082`)
- Gitignored (never committed): `benchmarks/data/user_lane_golden.jsonl` (bodies), `benchmarks/data/user_lane_probes.jsonl` (authored probe layer — it paraphrases private memory, so it is treated as a body)
- Bank: 51 goldens, sha256 `e29821b2…`, from 60 candidates (9 rejected)
- Axis strata: correction_retention 27 / staleness 12 / scope_contradiction 12; the four deferred axes are not built
- Category strata: procedural 34 / semantic 10 / guardrail_exception 5 / identity 2 — within 1.7pp of the measured 65/20/10/5 power-user distribution
- Rejects by reason: content_sensitive_excluded 2, source_conflict_unresolved 2, end_behavior_not_checkable 2, no_incident_in_bundle 1, duplicate_of_accepted_golden 1, bundle_incomplete 1 (that last one fired mechanically against real data, so the bundle gate is exercised outside its fixture)
- Source counts pinned at extraction: 90 `feedback_*` files across six projects (the plan's "~60" was stale), 61 Syndai `LEARNINGS.md` entries, 26 Syndai + 25 MemPhant `AGENTS.md` rule bullets
- Every golden records the observable correct behavior and the forbidden behavior, not only a retrieval target, so a future scorer grades end behavior; correction goldens are full bundles (rule + incident + how-to-apply) by enforced check
- Scope goldens are mirror pairs with identical temptations (worktree policy, ship path, CI polling, model-billing, database project, schema ownership), so only scope-keyed retrieval wins both sides
- Reproduce / verify: `cd /Users/sidsharma/Memphant-af-track-u && PYTHONPATH=. python3 scripts/user_lane_extract.py --check` (exit 0 = bank still reproduces the committed lock; drift or a parse break exits 1). Re-cut with the same command minus `--check`.
- Sources were opened read-only; nothing was written under `~/.claude/projects/` or in `/Users/sidsharma/Syndai-memphant-ref`
- Pre-existing unrelated failure in the full suite: `tests/test_public_launch_gate.py::test_public_sota_claim_policy_is_explicit_and_bare_claims_are_guarded` (shells out to `npm test`), reproduced on a clean stash
## Phase 1a-R - Track R repo-memory golden bank (accuracy-first program)

- Status: mined, deterministic, and **one preregistered check short of the bar** - not shipped as passing
- Bar preregistered and committed BEFORE mining: `docs/build-log/2026-07-30-track-r-golden-bar.md` (`0cf468eb`)
- Miner: `scripts/track_r_mine.py` + `tests/test_track_r_mine.py` (`bdc76b35`)
- Corpus: `nebius/SWE-rebench-openhands-trajectories` rev `35455389ab51bf5e2306bfd436ef72d0f98bf882`, CC-BY-4.0, materialized through the proven `scripts/materialize_public_code_lane.py` adapter - 495 attempts / 330 repositories / 64,055 events, corpus sha256 `c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669`
- Bank: 180 goldens, 60 per shape (state-churn / file-symbol-grounding / task-resumption), 156 distinct attempts, 129 distinct repositories, sha256 `6f549daaa3cc5be6dae095d044a50d17a8fd4ab82a23f2e973901cbb52a89b6d`
- Lock (only committed artifact): `benchmarks/data/track_r_repo_memory_golden.lock.json`; bodies and the 15-golden spot-check sample are gitignored
- Accept rate 180/412 = 0.4369, above the preregistered 0.40 dataset kill gate
- Rejects by reason: distractor_also_answers 82, insufficient_distinguishing_tokens 77, shape_target_met 63, identification_not_narrowed 51, adjudication_target_not_identified 12, too_generic_span 8, per_repo_cap 6, adjudication_parse_failed 2
- 14 of 15 bar checks pass. **Failing check: `with_distractors_ge_50pct`** - 75/180 (41.7%) goldens carry at least one explicitly adjudicated distractor, against a preregistered floor of 50%. The bar was NOT lowered. Diagnosis: the identification gate is stronger than the bar assumed - most accepted questions narrow the 64k-event corpus to the target alone, so there is no plausible distractor left to adjudicate. Every golden is still agent-adjudicated (100%), zero ship with an unadjudicated distractor, and zero ship with a distractor judged to also answer. Whether to amend that one threshold is an owner decision, not a miner decision.
- Generic-template failure mode is closed by measurement: 180 distinct question skeletons for 180 goldens, max single-skeleton share 0.56%, mean question/answer lexical overlap 0.052 (max 0.323)
- Spot-check sample emitted at `benchmarks/data/track_r_repo_memory_spotcheck.jsonl`, state `emitted_pending_owner_review` recorded in the lock; no published number may cite this bank until the owner advances that state
- Determinism: `python3 scripts/track_r_mine.py --verify-lock` re-mines from the warm cache and compares to the lock - currently OK (`6f549daaa3cc` == `6f549daaa3cc`, 180/180)
- Paid API spend: $0. Generation and adjudication ran entirely on subscription-model agent calls (688 cached replies, content-hash keyed), never OpenRouter
- Defect found and fixed mid-run: the adjudication prompt clipped the target event to 1,200 chars while the generator saw 3,000, so adjudicators were rejecting goldens whose answer sat past the clip. Fixed, and the whole adjudication wave was re-run rather than kept
