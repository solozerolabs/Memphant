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
## Accuracy-first Phase 1c retrieval probe + Phase 1b authoritative substrate-transfer replay

- Status: **executed**. Three arms, same 180 Track R goldens, $0 paid spend (no reader, no judge, no paid model call; `--embed-model off --mode fast`, `check_embed_model_key` never asked for a key). Each arm on its own fresh scratch DB via `scripts/with_scratch_db.sh`, own port, own outputs
- Worktree/branch: `/Users/sidsharma/Memphant-accuracy-first`, `accuracy-first`; base `eecc59ba`
- Commits: `27c00c95` (instrument seams + summary aggregator + tests), `d2f99e01` (executed measurement + committed summary + gitignore for run outputs). Not pushed
- Inputs verified before any DB or server process was created: corpus sha256 `c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669` (495 attempts / 64,055 events), golden sha256 `6f549daaa3cc5be6dae095d044a50d17a8fd4ab82a23f2e973901cbb52a89b6d` (180 goldens) — both match `benchmarks/data/track_r_repo_memory_golden.lock.json`, and every one of the 180 golden-to-event provenance edges (event_id pairing + exact span at char_start:char_end) re-verified

### Results (every number from an executed run)

| Arm | r@5 | r@10 | Provenance artifact |
|---|---|---|---|
| BM25 deterministic control | 0.761 | **0.806** | `docs/build-log/artifacts/track-r/phase1/bm25-provenance.json` |
| MemPhant cap-OFF | 0.450 | **0.506** | `docs/build-log/artifacts/track-r/phase1/memphant-capoff-provenance.json` |
| MemPhant cap-1200 | 0.450 | **0.506** | `docs/build-log/artifacts/track-r/phase1/memphant-cap1200-provenance.json` |

- Committed derived summary (no bodies, public CC-BY-4.0 corpus): `docs/build-log/artifacts/track-r/track_r_phase1_retrieval_summary.json`
- Run outputs under `docs/build-log/artifacts/track-r/phase1/` are gitignored — the evidence JSONLs carry full retrieved third-party event text
- Ingest/compile integrity per MemPhant arm: 64,055 evaluation events + 1 isolation sentinel ingested, `compiled=64056` jobs, `pending_jobs=0`, `dead_jobs=0`, 64,014 episodic units (42 exact-duplicate bodies deduplicated), `validate_compilation_summary` passed — so the numbers are not a half-drained-worker artifact. Both two-tenant negative recalls passed
- Paired at r@10: MemPhant cap-OFF vs BM25 — both 76, MemPhant-only 15, BM25-only 69, neither 20. cap-1200 vs cap-OFF — both 91, neither 89, **zero flips in either direction**

### Hypothesis A — bank saturation: NOT saturated

- BM25 r@10 = **0.806**, not 1.0. 35/180 goldens are unsolved by the lexical control, so the bank has 19.4pt of headroom in which a substrate win could have been expressed. This is not the SWE-ContextBench failure mode (baseline 10/10, required gain impossible)
- With-distractor subset (n=75) r@10 **0.787**; no-distractor subset (n=105) r@10 **0.819**. A 3.2pt gap: the 105 goldens that the `with_distractors_ge_50pct` check flagged are NOT a lexically-trivial subset, so the coverage miss reads as a threshold artifact of a stronger-than-assumed identification gate, not as a defect that inflates the control. (The 50% threshold decision remains the owner's; `bar_passed` stays `false`.)
- By shape, BM25 r@10: state-churn 0.683 / file-symbol-grounding 0.850 / task-resumption 0.883 — state-churn is the hardest stratum for the control, as its "a later touch, so a stale answer is wrong" precondition predicts
- Recorded mean question/answer lexical overlap 0.0517 (max 0.3226) is now corroborated by measurement rather than trusted as a proxy

### Hypothesis B — render-cap inertness: the cap RAN, and changed nothing

- Packed-items delta read BEFORE any retrieval delta, as preregistered. The cap **is** live on this corpus: per-item render chars fell **2,018,765 -> 1,926,115** total (mean 1147.0 -> 1094.4) and the largest single packed render fell **7,039 -> 4,011** chars. Offline pre-check agrees: 30,768 / 64,055 episode bodies (48.0%) exceed the 4-segment window and therefore mint contextual chunks, so the single-line-body inertness caveat from the instrument-proving run does not apply here
- Downstream it changed nothing: `packed_items_total` **1,760 in both arms** (mean 9.78/question), r@5 and r@10 byte-identical, zero per-question flips
- **The chat-lane Budget-drop pathology does not recur on code bodies.** `budget_share_of_in_pool_unpacked` = **0.0118** cap-OFF (1 of 85) and **0.0** cap-1200, against **1.00** (64/64) on the chat lane. The binding constraint here is the k=10 output-slot limit, not the 8192-token budget: 176/180 questions pack exactly 10 items, and the dominant in-pool-unpacked drop reason is `rerank` (56 cap-OFF / 57 cap-1200), which `admit_or_drop` emits only from the `acc.items.len() >= output_limit` branch — never from the budget branch
- Correct reading: this is a real measured null about the cap on code bodies (it ran, at a measured render-size delta, and moved no retrieval), not the false null "the cap did not run". Per the plan's own conditional, pack_render_cap is a chat-lane footnote on this evidence and Phase 2 drops in priority

### Where the MemPhant deficit actually lives

- Candidate stage is nearly perfect: gold reaches the pool on **176/180** questions (`gold_in_pool_rate` 0.978, median pool size 124). Only **91** survive packing. `absent_from_pool` is 4
- Of the 85 `in_pool_unpacked` misses, **56 had their best gold pool unit already inside the top-10 fused ranks** (median gold fused rank 8; hits have median 2) and still did not reach the pack. 29 sat beyond k
- So the loss is **pack selection**, not retrieval. Any Phase 3 coding-lane work aimed at embeddings or recall breadth would be aimed at the 2.2pt part of the problem
- Fairness note that strengthens the finding, not weakens it: MemPhant recall is bound per attempt (`bind_attempt_context`), so each query searches only its own trajectory (~124 candidates), while BM25 ranks all 64,055 events corpus-wide. MemPhant loses by 30.0pt at r@10 **despite** the narrower haystack

### Kill gate

- The Phase 1 kill gate "MemPhant does not beat BM25 on retrieval" is **TRIPPED as measured**: 0.506 vs 0.806 at r@10, 0.450 vs 0.761 at r@5, and BM25 wins 69 questions MemPhant loses against 15 the other way. The gate's consequence — ownership decision (d) defaults to "Syndai keeps its tables" until the substrate wins — is the owner's call, recorded here as measurement, not decided here
- Caveat that belongs with the number: the bank's spot-check state is still `emitted_pending_owner_review`, so per the bar no published number may cite this bank yet

### Reproduce

```
cd /Users/sidsharma/Memphant-accuracy-first            # branch accuracy-first, commit d2f99e01
cargo build --release --bin memphant-server --bin memphant-worker --bin memphant-cli

# arm 1 - BM25 deterministic control (~15 min, no DB)
python3 scripts/code_lane_run_deterministic.py \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1/bm25-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1/bm25-provenance.json --k 10

# arm 2 - MemPhant cap-OFF   (~40 min: 670s ingest, 906s compile, 728s recall)
python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1/memphant-capoff-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1/memphant-capoff-provenance.json \
  --embed-model off --mode fast --k 10 --budget-tokens 8192 \
  --label track-r-capoff --port 39431 \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli

# arm 3 - MemPhant cap-1200: arm 2 plus `--pack-render-cap 1200`, with
#   --label track-r-cap1200 --port 39433 and the cap1200 output paths

python3 scripts/track_r_phase1_summary.py \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --bm25 docs/build-log/artifacts/track-r/phase1/bm25-provenance.json \
  --cap-off docs/build-log/artifacts/track-r/phase1/memphant-capoff-provenance.json \
  --cap-1200 docs/build-log/artifacts/track-r/phase1/memphant-cap1200-provenance.json \
  --out docs/build-log/artifacts/track-r/track_r_phase1_retrieval_summary.json
```

- Instrument changes were three small seams, not a new instrument: `corpus_contract()` accepts the golden lock's corpus block under either committed key (`extraction` from the v3 miner, `corpus` from the Track R miner) with `corpus_bytes` optional since sha256 already witnesses it; `retrieval_query()` falls back to the golden's `question` so both arms query the identical string (the gold-leak guard still fires, and all 180 pass it); `pack_drop_diagnosis`/`pack_drop_summary` additionally record packed-item counts and per-item render sizes so an inert cap arm can never be misread as an ineffective one
- Runnable check: `python3 -m pytest tests/test_code_lane_run_memphant.py tests/test_track_r_phase1_summary.py -q` -> 36 passed (6 new cases: the query fallback + its leak guard, the lock-key normalization, a lock without `corpus_bytes`, the render-size witness, the stage decomposition, and both readings of the hypothesis-B witness)

## 2026-07-30 — Phase 1c: the scoped-haystack arm (Track R, executed, $0)

The three Phase 1b/1c arms were compared across two constructs at once, in
opposite directions, so neither the 0.8056-vs-0.5056 gap nor its retraction
could be read off them:

1. **Stage.** BM25's `hit_at_10` was a plain ranked top-10; MemPhant's was the
   10 items that survived packing (`packed_size` ∈ {0, 10}).
2. **Haystack.** BM25 ranked all 64,055 corpus events; MemPhant's candidate pool
   never leaves the one coding attempt its recall context is bound to.

This entry removes both. No new MemPhant arm was run: the ranked stage is read
off the committed per-question provenance.

### MemPhant's actual scoping rule (determined from the runner, then verified)

`code_lane_run_memphant.bind_attempt_context()` binds
`scope_ref`/`actor_ref`/`agent_node_ref` = `code-lane:*:{attempt_id}` (subject =
`run_id`), and the evaluation loop recalls through
`evaluation_contexts[golden["provenance"][0]["attempt_id"]]`. All 180 goldens
have single-attempt provenance, so the haystack is exactly one attempt. Empirical
witness in the artifact, all 180/180:

- scoped BM25 `documents_searched` == that attempt's event count (0 violations)
- MemPhant `pool_size` <= the attempt's unique contextual-body count
- MemPhant `pool_size` <= scoped BM25 `documents_searched`, ratio mean 0.953
- median haystack: scoped BM25 126 events vs MemPhant `pool_size` 124

MemPhant's pool is a *lexically prefiltered subset* (`websearch_to_tsquery` +
`ts_rank_cd`, `limit 200`, `memphant-store-postgres/src/store.rs:2148`) of the
same attempt, so the scoped control is given the larger of the two haystacks —
the residual asymmetry now runs against BM25, not for it. It cannot be closed
further from committed artifacts: the provenance records pool *counts*, not pool
unit ids.

### Construct-identity checks

- **Gold predicate: identical.** Both runners grade with
  `gate_common.provenance_hit`. The fused-rank stage uses
  `gate_common.contains_gold` via `gold_bearing_units`, the same matcher
  `provenance_hit` calls per span, and all 180 goldens carry exactly ONE
  required span (asserted in the comparison, not assumed), so
  `gold_fused_rank <= k` **is** `provenance_hit` at k.
- **Query string: identical.** The bank has no `retrieval_query` field, so
  `retrieval_query()` returns `question` for both arms. The control now calls
  that seam directly instead of reading `golden["question"]`; the change is
  numerically inert — the re-run corpus-scope arm is **byte-identical** evidence
  and an identical per-question hit vector (r@5 0.7611, r@10 0.8056).

### Executed numbers (n=180, paid spend $0, no reader, no judge, no DB)

| arm | haystack | r@5 | r@10 |
|---|---|---|---|
| BM25, corpus scope (original control) | 64,055 events | 0.7611 | 0.8056 |
| **BM25, attempt scope** | median 126 events | **0.8278** | **0.8944** |
| MemPhant fused ranked top-k | median pool 124 | 0.6278 | 0.8167 |
| MemPhant packed top-10 (as previously reported) | median pool 124 | 0.4500 | 0.5056 |

Scoped BM25 subsets — with adjudicated distractor (75): r@5 0.7600, r@10 0.8667;
without (105): r@5 0.8762, r@10 0.9143. By shape: file-symbol-grounding
0.9000/0.9167, state-churn 0.7167/0.8333, task-resumption 0.8667/0.9333.
MemPhant fused by shape (r@5/r@10): 0.5667/0.8333, 0.6500/0.7833, 0.6667/0.8333.

Paired, same haystack and same stage (MemPhant fused vs scoped BM25):

| | both | MemPhant only | BM25 only | neither | discordant |
|---|---|---|---|---|---|
| @10 | 133 | 14 | 28 | 5 | 42 |
| @5 | 99 | 14 | 50 | 17 | 64 |

@10 by shape (MemPhant-only / BM25-only): file-symbol-grounding 4/9, state-churn
8/11, task-resumption 2/8. With distractor 8/9; without 6/19. Reference pairing
of MemPhant's *packed* top-10 vs scoped BM25 @10: 80 both, 11 MemPhant-only, 81
BM25-only. Gold rank 1: scoped BM25 91/180, MemPhant fused 59/180. Gold reaches
MemPhant's pool on 176/180; scoped BM25 fails to surface gold in its top-10 on
19/180.

Artifacts: `docs/build-log/artifacts/track-r/track_r_phase1c_scoped_bm25_comparison.json`
(committed); raw arms under the gitignored
`docs/build-log/artifacts/track-r/phase1/` as
`bm25-attempt-scoped-{provenance.json,evidence.jsonl}` and
`bm25-corpus-scoped-{provenance.json,evidence.jsonl}`.

No kill-gate verdict, no ownership call and no bar amendment is recorded here —
those remain owner decisions.

### Reproduce

```
cd /Users/sidsharma/Memphant-accuracy-first            # branch accuracy-first

# scoped BM25 arm (~4 s, no DB, no server, no model call)
python3 scripts/code_lane_run_deterministic.py \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1/bm25-attempt-scoped-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1/bm25-attempt-scoped-provenance.json \
  --k 10 --scope attempt

# corpus-scope arm re-run, the inertness control for the query seam (~15 min)
python3 scripts/code_lane_run_deterministic.py \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --out-evidence docs/build-log/artifacts/track-r/phase1/bm25-corpus-scoped-evidence.jsonl \
  --out-provenance docs/build-log/artifacts/track-r/phase1/bm25-corpus-scoped-provenance.json \
  --k 10 --scope corpus

python3 scripts/track_r_scoped_bm25_compare.py \
  --golden benchmarks/data/track_r_repo_memory_golden.jsonl \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --bm25-corpus-scope docs/build-log/artifacts/track-r/phase1/bm25-corpus-scoped-provenance.json \
  --bm25-attempt-scope docs/build-log/artifacts/track-r/phase1/bm25-attempt-scoped-provenance.json \
  --memphant docs/build-log/artifacts/track-r/phase1/memphant-capoff-provenance.json \
  --out docs/build-log/artifacts/track-r/track_r_phase1c_scoped_bm25_comparison.json
```

- Runnable check: `python3 -m pytest tests/test_code_lane_run_memphant.py tests/test_track_r_phase1_summary.py -q` -> 39 passed (3 new: `--scope attempt` hands BM25 exactly the attempt `bind_attempt_context` binds and `--scope corpus` keeps the corpus; the paired flip counts; the scoping witness flagging a haystack mismatch)
- Input contract verified before the runs: `corpus_sha256 c008142e...4c9669`, `golden_sha256 6f549daa...2a89b6d`

## 2026-07-30 — Phase 1d: the packing displacement fix (Track R, executed, $0)

Answers Phase 1 §5(b). Worktree `/Users/sidsharma/Memphant-af-packing`, branch
`af-packing`. Full write-up: `docs/build-log/2026-07-30-packing-rank-order-fix.md`.

**The 56 "displaced" golds were two defects, not one.** Splitting them was the
first result, and it needed an instrument change: 28 of the 56 carried no drop
reason, which was ambiguous between "never took a packed slot" and "took one and
was rendered without the span". Recording the packed items' unit ids resolves it:

| of the 56 | drop reason | gold units in packed slots |
|---:|---|---|
| 27 | `rerank` | 0 — rank displacement |
| 28 | *(none)* | 1 or 2 — **render loss, not displacement** |
| 1 | `budget` | 0 |

**Mechanism (cause 1).** `admit_or_drop`'s output-full branch let a candidate
from fused ranks 11–64 evict an already-packed item whenever it scored higher
under `packing_relevance_score` — fused score *plus* exact/lexical/overlap/
retrievability, which is not the order the pack was handed. Unreachable in Fast
until R1.5-T0 widened the scan window past `k`. The `rank_based_ordering_active`
gate switched the contest off for Deep/cross-encoder/submodular, so it ran in
exactly one configuration: the plain Fast default. The 2026-07-12 verdict that
found this gate "measured-permanent" was taken on the cross-rerank arm, where the
contest never executes — it measured the gate's *off* state. Worst case observed:
a gold at **fused rank 3** evicted from a full pack.

**Fix** (`03fa1266`): once the output is full the established order wins and the
late candidate is dropped, never swapped in; `rank_based_ordering_active` goes
with the contest. The budget-driven replacement below it is untouched — that one
is a real substitution, not a re-score.

**Result** (baseline arm re-executed, reproduces the committed Phase 1b run to
the digit — 91 hits, 1760 packed items, 2,018,765 packed chars):

| arm | r@5 | r@10 | packed |
|---|---:|---:|---:|
| baseline | 0.4500 | 0.5056 | 91/180 |
| rank-order fix | 0.4611 | **0.6278** | **113/180** |

- @10 paired: **22 gains, 0 losses**, McNemar exact **p = 4.77e-07**.
- @5 paired: 3 gains, 1 loss, p = 0.625 (ns).
- **22 of the 56** preregistered displaced golds recovered (not 27 — 5 of the
  eviction cases now reach a slot and hit cause 2 instead).
- Displacement **eliminated**: golds at fused rank ≤10 that are `Rerank`-dropped
  go **27 → 0**; the best-ranked gold that still loses the contest sits at rank
  12, i.e. genuinely below the cut.
- `fused_top10_ceiling` 147/180 in both arms — retrieval untouched.

**Chat lane: inert, not merely neutral.** Two LME-S arms (dataset `e4667bed…`,
`--sample 178 --seed 20260710`), r@5 = r@10 = 0.6145 in both, **0 flips** across
166 scored questions, and after normalizing per-run episode UUIDs the packed
context is **byte-identical on all 178 questions**. Mechanistically forced: LME-S
packs 2–9 items and never reaches k=10, so the branch is unreachable there — the
other side of the rung-7 budget-bound pathology. `pack_render_cap` untouched and
still `undecided`.

**Not bundled in — cause 2 (render loss), 33 of the 34 remaining rank-≤10
misses.** `packed_render` charges each chunk block its header on top of its body,
so full coverage always costs more than the whole body and an uncapped chunked
item can never emit all of itself; it drops chunks while charging nearly the
whole-body price. The one-line fallback was implemented, measured, and reverted:
it raises per-item cost (wrong direction for the chat lane's budget-bound pack),
it collides with `sibling_gather`'s own invariant test, and it is adjacent to the
`undecided` `pack_render_cap`. It is the next localized target and needs its own
paired chat-lane arm. Closing it takes the pack from 113 to ~146 against a
147/180 fused ceiling.

**Owner decision:** `test_canonical_census_source_inventory_covers_declared_campaign_code`
fails on this branch and passes at base `a96c289c` — any `memphant-core` edit
moves `source_set_sha256`, which the v5 campaign census pins. Not bumped; v5 is
parked. Two other failures (`test_public_sota_claim_policy_…`, Syndai spec drift)
are pre-existing at base.

Commits (none pushed): `03fa1266` → `cc69a608` → `26a3c032` → `4628d88b`.
Artifacts: `docs/build-log/artifacts/track-r/track_r_phase1d_packing_rank_order.json`,
`docs/build-log/artifacts/rung7-packing-reader-gate/phase1d/chat-lane-nonregression.json`
(both committed; per-question dumps gitignored, third-party bodies).
## Phase 1r — code-lane retrieval fix (2026-07-30, branch `af-retrieval`)

Answers the Phase 1c kill gate on its own construct. Build log:
`docs/build-log/2026-07-30-phase1r-retrieval-bm25.md`. $0 paid spend.

`MEMPHANT_LEXICAL_SCORER` (default `overlap`, byte-identical off-path) replaces
fusion's two token-overlap passes with ONE Okapi BM25 pass over the recall
candidate pool. Same 180 goldens (`6f549daa…`), same attempt-scoped haystack,
same stage (fused ranked top-k), exact McNemar:

| arm | embed | r@5 | r@10 | @5 vs scoped BM25 | @10 vs scoped BM25 |
|---|---|---:|---:|---|---|
| scoped BM25 control | — | 0.8278 | 0.8944 | — | — |
| `overlap` (default) | off | 0.6278 | 0.8167 | 14/50 p=0.00001 ✗ | 14/28 p=0.0436 ✗ |
| `bm25-control` | off | 0.8722 | 0.9056 | 10/2 p=0.0386 ✓ | 5/3 p=0.727 null |
| **`bm25-code`** | off | **0.9500** | **0.9611** | 24/2 p=0.00001 ✓ | 15/3 p=0.0075 ✓ |
| `overlap` + dense | small | 0.7778 | 0.9000 | 15/24 p=0.200 null | 16/15 p=1.000 null |
| `bm25-code` + dense | small | 0.9111 | 0.9556 | 24/9 p=0.0135 ✓ | 17/6 p=0.0347 ✓ |

- Arm 0 reproduces the committed Phase 1c baseline EXACTLY (fused 113/147, pool
  176, rank-1 59, packed buckets 4/91/85), and the re-run scoped BM25 control's
  per-question vector is byte-identical to `track_r_phase1c_scoped_bm25_comparison.json`.
- **Worked:** BM25 instead of token overlap (+0.244 r@5 / +0.089 r@10) and
  code-aware tokenization on top of it (+0.078 / +0.056). Gold at rank 1
  59 → 135 vs the control's 91.
- **Did not work:** dense embeddings. Big lift over `overlap` (+35/−8 @5
  p=0.00004) but only to a null vs BM25 (p=0.200 / p=1.000), and ON TOP OF
  `bm25-code` it is −10/+3 @5 (p=0.092) and −3/+2 @10 (p=1.000). No hybrid.
- **Not taken:** the briefed Postgres `english`→`simple` swap. Pool covers a
  median 0.985 of the attempt, no attempt hits the 200-row cap, and all 4
  residual pool misses are `below_trust_floor` drops of the WHOLE attempt from 4
  benign queries tripping `high_risk_action_query` (`create`+`claim`,
  `create`+`registry`, `script`+`library`, `script`+`support`) — a policy
  ceiling of 176/180, not a tokenizer one. Measured migration cost on the real
  64,013 unit bodies: 8.0 s table rewrite + GIN rebuild under ACCESS EXCLUSIVE,
  table 128.9 MB → 149.6 MB (+16.0%).
- **Chat lane improves**, does not regress: LME-S retrieval-only, seed 1,
  n=120 (111 graded) 66 → 75, +10/−1, exact p = 0.0117.
- **Blocking (owner decision, nothing re-pinned):** `service.rs` is sha-pinned by
  `longmemeval_v2.state_aware_full.v{1..5}.json` and `gate_runtime.py` by a
  committed SWE-ContextBench rehearsal; both pins now drift. Pre-existing and
  unrelated: `check_spec_drift.py` dirty, and the launch-gate test needs
  `playwright`.
- Adapted OSS: **none**. Textbook BM25 written against this repo's own control.

Runnable check: `python3 -m pytest tests/test_track_r_retrieval_arm_compare.py
tests/test_code_lane_run_memphant.py tests/test_gate_runtime.py -q` → 81 passed;
`cargo test --all-targets --all-features` → 73 suites, 0 failures. Reproduce
commands in the build log.

## Phase 1e — the two fixes measured together (2026-07-30)

Cost: **$0**. Deterministic retrieval only — no reader, no judge, no paid model
call, embeddings OFF (dense was measured null-to-negative on this lane in Phase
1r and was not re-enabled). One new arm executed at `a6d1d9b0` (both fixes
merged), `--lexical-scorer bm25-code --embed-model off --mode fast --k 10
--budget-tokens 8192`, cap off, release binaries built in this worktree, own
scratch Postgres auto-dropped, worker fully drained before any query
(`compiled=64056`, `pending_jobs=0`, `dead_jobs=0`, 64,014 units after 42
dedups). Golden sha256 `6f549daa…` and corpus sha256 `c008142e…` verified before
the run. Nothing else was executed: the three baselines are the committed runs,
reused per-question.

**Two stages, combined build (n = 180):**

| stage | r@5 | r@10 |
|---|---:|---:|
| fused (ranking) | **0.9500** (171) | **0.9611** (173) |
| packed (what reaches a reader) | **0.7278** (131) | **0.7722** (139) |

**Paired, exact McNemar:**

| comparison | k | after-only | before-only | exact p |
|---|---|---:|---:|---:|
| combined fused vs scoped BM25 control (0.8278/0.8944) | @5 | 24 | 2 | **1.0e-05** |
| " | @10 | 15 | 3 | **0.00754** |
| combined packed vs packing-fix-only packed (113) | @10 | 28 | 2 | **8.68e-07** |
| " | @5 | 51 | 3 | **2.92e-12** |
| combined packed vs original packed (91) | @10 | 49 | 1 | **9.06e-14** |
| " | @5 | 53 | 3 | **8.14e-13** |
| combined packed vs retrieval-fix-only packed (97) | @10 | 43 | 1 | **5.12e-12** |

**Fused→packed gap: 34 questions at k=10** (173 → 139, 0.9611 → 0.7722, −0.1889)
and 40 at k=5. That is the remaining packing loss and the size of the next
target. Its composition: 30 render losses (`not_in_dropped_items` with the gold
unit in a packed slot — the defect §6 of the packing build log quantified and
deliberately did not fix), 4 `budget`, 3 `rerank` (all at fused rank ≥ 11, i.e.
genuinely below the cut), plus 4 `absent_from_pool` policy drops.

**The fixes are super-additive, not merely additive.** Full 2×2 on packed hits
of 180 — neither 91, packing-only 113 (+22), retrieval-only 97 (+6), both
**139** (+48). Additive prediction was 119; the interaction term is **+20
questions at k=10** and **+33 at k=5**. Mechanism: better ranking supplies more
top-10 golds *and* the packer no longer lets rank-11–64 candidates evict them —
each fix converts questions the other one alone could not. Retrieval-only packed
gained just 6 because the old packer discarded the ranking win (79 in-pool
golds unpacked, 44 of them `rerank`-evicted); with the packer fixed the same
retrieval gain lands. Fused is unchanged by the packing fix (173 in both, 147
in both unfixed arms), which is the check that no stage bled into the other.

Cross-branch pairing is licensed by evidence, not assumption: the unfixed
baseline was executed independently in two worktrees and all 180 questions agree
on `hit_at_5`/`hit_at_10`/`gold_fused_rank`/`gold_in_pool`/`packed_size`/
`gold_drop_reason`/`bucket`; only `track_r_064`'s `pool_size` differs (131 vs
120), with the same outcome.

**Not production-representative, and not a claim.** The bank is lexically biased
toward its targets (question→target token coverage 0.396 vs 0.094 to a random
non-target in the same attempt; 105/180 questions narrow to exactly one event),
and a third-party measurement puts BM25 at R@10 ~18 on genuine NL→code queries.
These are numbers on this instrument. No checkbox, default, cutover, deployment
or SOTA claim moves; the Phase 1r pin collision (`service.rs` under the v5
census, `gate_runtime.py` under the SWE-ContextBench rehearsal) is unchanged and
still an owner decision.

Artifact: `docs/build-log/artifacts/track-r/track_r_phase1e_combined_fixes.json`
(committed). Per-question evidence and the three analyzer outputs under
`…/track-r/phase1e/` are gitignored under the same rule as `phase1/`, `phase1d/`
and `phase1r/` — they carry third-party event bodies.

---

## 2026-07-30 — W3.1 governance-core spec + W5.3 restraint reconciliation (`af-w3-spec`)

Docs and one test only. No Rust touched.

**W3.1 — the R3 governance core now has a spec.** It previously existed only in
`docs/reports/2026-07-11-prosumer-memory-campaign-report.md:231-275`, so
"finish all substrates" had no contract to finish against. Written into the
owning docs per `00` §1: the memory model into `04`, the ops half into `14`.

- `04` **§13 Governance Core** (new): §13.0 verified starting state with a
  file:line per claim; §13.1 the typed write-router — dispatch on `kind` first,
  one arm per kind, invariants RW-1..RW-8 (kind-totality with **no `_` arm** as
  the enforcement mechanism, arm purity/no-LLM, own-kind writes, append-only
  transaction time, idempotence, traced decisions, no widening/no trust
  escalation, principal fidelity) plus a per-arm contract table
  (trigger/operation/must-guarantee/forbidden/code-state); §13.2 the kind
  extension — `preference` and `working_state` policy rows written and passing
  the `04` §0.1 admission test but **PROPOSED-unminted**, with `knowledge`
  resolved as an arm name rather than a variant; §13.3 `retention_tier` as a
  state machine — transition table with predicates, exactly one writer
  (`tier_episode`), invariants RT-1..RT-6, and five conformance tests;
  §13.4 demotion-vs-deletion as two orthogonal mechanisms with contract
  DM-1..DM-5 and the storage mechanism left open; §13.5 principal-scoped
  multi-agent access PS-1..PS-5; §13.6 the specced-but-unbuilt register;
  §13.7 six open decisions, each with the evidence that closes it.
- `04` §2.4, §4.2, §8, §12: UNBUILT markers and corrected pointers where the
  existing prose read as description of code that does not exist.
- `14` **§3.3 Governance-Core Jobs** (new): the ops contract for `tier_episode`
  (schedule, inputs, enqueue sources, batch bounds, idempotency, trace, failure,
  never-does) and `promote_procedure`; unit demotion deliberately has no job
  because its mechanism is `04` §13.7 OPEN-4.

`chain_head` is specified as **derived, not a column** — it is §7.3a's open
generation, already enforced by the partial-unique index. Adding a column would
be a second source of truth.

**Correction to the W3.2 brief, recorded rather than silently applied.**
`Preference` is minted as a real kind. `Knowledge` is **not**: run through
`04` §0.1 it fills the same policy row as `semantic` on all six columns, and a
sixth variant would fork the bitemporal supersession contract. It is the
router-arm name (`knowledge_arm`); the rename of the enum variant is OPEN-2 with
its cost (breaking migration across `04`/`05`/`06`/`08`/`20`, both SDKs, every
stored `kind` string) stated.

**W5.3 — the restraint gate no longer rejects its own passing run.** `27` §1 and
the scorecard named OP-Bench/PS-Bench while
`tests/test_restraint_launch_gate.py` asserted
`benchmark in {"op-bench","ps-bench"}` on `pass`, and the only pinned, adapted,
executed instrument is MemSyco. `27` §1 substitutes **MemSyco-Bench** with the
justification recorded (OP-Bench has no runnable release; PS-Bench has no
license; MemSyco is MIT, pinned by revision + per-file sha256, native scorer,
and measures the same construct across its five tasks). `26` §3 gains
D-2026-07-30c with the scope limit, the non-overlap MemSyco does **not** cover
(OP-Bench's irrelevance/sycophancy/repetition taxonomy; PS-Bench's
intent-legitimation, which stays a `06` §9 threat row), and a reopen test. The
test admits `memsyco-bench` alongside both older names. **No substantive
threshold moved**: drop ≤ 0.15, sample ≥ 50, CI upper ≤ 0.15, `05` §1.5 gate
mandatory on breach, pinned-block in-scope.

**Verification.** `python3 scripts/check_spec_drift.py` → `spec_drift=clean`
(mirror synced file-by-file for only the five files touched, after confirming
each matched this branch's base — the Syndai tree's other modifications were
left alone). `python3 -m pytest tests/` → **1028 passed, 15 skipped, 1 failed**;
the single failure is the pre-existing environmental
`test_public_launch_gate.py::test_public_sota_claim_policy_...`
(`sh: playwright: command not found`), unrelated to this change.

**No checkbox moved** — STATUS gains one note; checked-box count is 21 before
and after.
## W3.3 — the `memphant_app` served role (2026-07-30, branch `af-w3-rls`)

**The hole.** `20260703_001` put `ENABLE` + `FORCE ROW LEVEL SECURITY` on 28
tables with 27 `_tenant_isolation` policies keyed on
`memphant.current_tenant_id()`. None of it was active on the served path:
`connect_pool`'s `after_connect` set only `search_path`, `SET ROLE` appeared
zero times outside `tests/`, no migration/script/CLI/profile ever created a
login role that was a member of `memphant_app`, and every packaging path
shipped a bypassing credential (compose: the initdb superuser, plus the stale
bare `DATABASE_URL` the runtime rejects; Neon: `memphant_owner`, which holds a
`using(true)` owner policy; Supabase: the project superuser). Tenant isolation
rested entirely on application predicates. The tests did what production did
not.

**What landed.**

1. `20260730_004_served_login_roles` — one NOINHERIT **login** role per served
   capability (`memphant_{app,authn,worker,provisioner}_login`), each a member
   of exactly one capability role, with `revoke all on schema memphant` so the
   login itself reaches nothing directly. Passwordless on purpose: migrations
   live in git, and a passwordless LOGIN role cannot authenticate under
   scram/md5. `scripts/provision_login_roles.sh` is the provisioning step; it
   refuses to hand back a credential that is SUPERUSER or BYPASSRLS.
2. `PgStore` pools now carry the capability role they serve and issue an
   explicit `SET ROLE` in `after_connect`. A pool is shared between
   capabilities only when the credential **and** the role match, so
   `connect_app` no longer collapses the disjoint app and authn capabilities
   onto one connection when both URLs are the same (which every shipped profile
   and the e2e probe do).
3. Fail-closed startup assertion: the pool refuses to serve unless
   `current_user` is exactly the capability role and is neither `rolsuper` nor
   `rolbypassrls`. Verified by deleting the `SET ROLE` and re-running — the
   store refuses with the actionable message rather than serving without RLS.
4. Packaging: compose gained a one-shot `bootstrap` service (new Dockerfile
   stage, so the runtime image gains no psql/python) that applies migrations
   and provisions credentials; server/worker wait on it and use the split
   credentials. Both provider profiles document `DATABASE_URL` as the migrator
   credential and add the three served URLs. `memphant db bootstrap-check` now
   rejects a served credential that reuses the migrator login or names a known
   RLS-bypassing role.
5. `crates/memphant-store-postgres/tests/served_path_rls.rs` — hands
   `connect_app` an ordinary member login and runs a bare cross-tenant query
   through the **store's own pool** (no `set local role` in the harness), with
   an unrestricted control asserted to leak so the zero is not vacuous.
   `e2e_probe.sh` now runs the real server and worker under probe-minted
   non-superuser member logins; its standing "RLS never fires here" note is
   gone because it is no longer true.

**Superuser dependencies found (the point of the exercise).**

- `hot_path_slo_pg.rs` drained the reflect queue through the **app** store.
  `claim_reflect_jobs` is granted to `memphant_worker` alone, so this worked
  only while the app credential was a superuser. Fixed by draining on a worker
  store, which is what production does. No grant was widened.
- `memphant admin create-key --subject-id …` calls `resolve_memory_context`,
  which reads `context_binding`/`subject`. The provisioner capability has
  execute on three SECURITY DEFINER functions and **no table access at all**
  (verified: `permission denied for table context_binding`), a boundary
  `role_matrix.rs` asserts deliberately. `connect_provisioner` therefore stays
  unrestricted, documented in the code. Making the admin CLI least-privilege
  needs a second app-capability connection or a narrower provisioning
  function — a real follow-on, not something to buy by widening the grant.
- Latent, pre-existing, **not** fixed: `memphant-cli`'s `connect_pg` builds the
  pool in one `block_on` runtime and every operation runs in a second. The
  pool's connections belong to the dead first runtime, so any acquire that has
  to re-establish times out. Adding one query to the connect path was enough to
  surface it as `pool timed out while waiting for an open connection`. The
  three `admin` subcommands each pay this.
- Role-level GUCs (`alter role memphant_app set statement_timeout = '30s'`)
  apply at **login**, not at `SET ROLE`, so the per-role timeouts still do not
  reach the served session. They did not before this change either (the server
  logged in as the superuser), so this is a standing gap, not a regression.

**Verification** (`af-w3-rls`, commits `07a3b788`, `0f401dea`, `6009645b`,
`0b1845ec`):

- `cargo fmt --all --check` clean; `cargo clippy --workspace --all-targets`
  clean.
- `cargo test --workspace --no-fail-fast`: 674 passed, 1 failed, 94 ignored.
  The one failure, `memphant-eval syndai_coding_continuity_fixture_families_pass`,
  reproduces on the stashed branch base — pre-existing, not from this work.
- `python3 -m pytest tests/ -q`: 1026 passed, 15 skipped, 3 failed before
  repair; `test_wsa_migration_contract` was mine (migration count 3→4) and is
  fixed. `test_public_launch_gate` and `test_repo_contract::…spec_drift…` fail
  identically on the stashed base.
- Scratch-DB leg (`with_scratch_db.sh … -p memphant-store-postgres -p
  memphant-worker -- --ignored --test-threads=1`): **81 passed, 0 failed.**
  Two tests needed repair first (both recorded above as superuser
  dependencies): `hot_path_slo_pg` and `pg_store_contract`'s readiness probe,
  which called `connect_worker` with a login granted only `memphant_app`.
- `scripts/e2e_probe.sh`: ALL CHECKS PASSED with the server and worker running
  as `memphant_app` / `memphant_authn` / `memphant_worker`. No missing grant
  surfaced across retain, recall, MCP, correct, forget, mark, traces or
  restart durability.
- `docker compose config` valid; the `bootstrap` stage was built and run
  against a real database — 4 migrations applied, three credentials issued.
  A full `docker compose up --build` was **not** run (multi-minute release
  build); the server/worker containers are unexercised end to end.
- `db lint` clean for all three providers; two-migration dry-run reports
  `migration_plan=4`.

**Residual gaps.**

- The spec-25 correction adds `25-…md` to `check_spec_drift.py`'s output. That
  check was already dirty at the branch base (5 files); the same one-paragraph
  correction should be mirrored into the private spec copy, which lives outside
  this worktree.
- The compose stack is verified by `config` + a live bootstrap run, not by a
  full `up --build`.
- The CLI two-runtime pool bug and the admin CLI's superuser dependency are
  reported, not fixed.
