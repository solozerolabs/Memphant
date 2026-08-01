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

`MEMPHANT_LEXICAL_SCORER` (default `overlap` **at the time of this entry** —
flipped to `bm25-code` on 2026-08-01, see
`docs/build-log/2026-08-01-dense-default-on.md`; `overlap` is now the opt-out)
replaces fusion's two token-overlap passes with ONE Okapi BM25 pass over the
recall candidate pool. Same 180 goldens (`6f549daa…`), same attempt-scoped haystack,
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
## Phase 1w — the render-loss fix (2026-07-30, branch `af-w1-render`)

Cost **$0**, deterministic retrieval only, no reader/judge/paid call. Full
record: `docs/build-log/2026-07-30-packing-render-loss-fix.md`.

Closes the last large packing loss on the coding lane, the one
`2026-07-30-packing-rank-order-fix.md` §6 quantified and deliberately reverted.

**Mechanism.** `packed_render` charges each chunk block its provenance header on
top of its body while the per-item render budget is the whole body, so full
chunk coverage always costs MORE than the whole body: an uncapped chunked item
can never emit all of itself. Trace, `track_r_021`: gold at fused rank 1, gold
unit in packed slot 0, and the slot rendered 578 chars of one chunk block
(`[episode …] [segments 1-4]`) instead of the 696-char body that carries the
span. 30 of the baseline's 41 misses were this.

**Fix (`f67f2b2a`).** A post-fill pass, the twin of `sibling_gather_pass`: an
item rendered from a PARTIAL chunk selection takes its whole body — a superset,
since chunk bodies are byte slices of it — when the pack's LEFTOVER budget
covers the difference. Not the reverted admission-time fallback: admission is
untouched, so the packed item set and every drop record are byte-identical, a
budget-bound pack is a no-op, and `sibling_gather` keeps first claim on the
leftover (its invariant test is unchanged and passing). `pack_render_cap`
suppresses the pass entirely, so that lever stays `undecided` and unentangled.

**Coding lane** — 180 Track R goldens (`6f549daa…`), corpus `c008142e…`,
attempt-scoped, `--lexical-scorer bm25-code --embed-model off`, k=10,
budget 8192, cap off, worker drained (`done_jobs=64056`, `pending_jobs=0`,
`dead_jobs=0`) identically in both arms. Baseline re-executed, not remembered,
and reproduces the committed combined build to the digit (139 packed, 30 render
losses).

| arm | r@5 | r@10 | packed |
|---|---:|---:|---:|
| before `ccaa9e1c` | 0.7278 | 0.7722 | 139/180 |
| **after `f67f2b2a`** | **0.9222** | **0.9333** | **168/180** |
| fused ceiling | — | 0.9611 | 173/180 |

Paired exact McNemar: **@10 29 gains / 0 losses, p = 3.73e-09**; @5 35/0,
p = 5.82e-11. **29 of the 30 render losses recovered** — the holdout
`track_r_049` is genuine budget pressure (pack already at ~8.1k of 8192 tokens)
and correctly not upgraded. Misses 41 → 12 (render 30→1, budget 4, rerank 3 all
at fused rank ≥ 11, absent-from-pool 4). `fused_top10_ceiling` 173 in BOTH arms
— no retrieval or ranking behaviour moved.

**Chat lane — the gate, and it passes.** Two `bench-lme` arms, dev split
`e4667bed…`, `--sample 178 --seed 20260710 --k 10 --budget-tokens 8192 --pool 64
--embed-model small`, product-default `overlap` scorer (product default as of
that run; `bm25-code` since 2026-08-01).

- r@5 and r@10 **0.6145 in both arms**, reproducing the committed rung-7
  baseline exactly; 102/166 hits both; **0 flips in either direction**,
  McNemar p = 1.0; per-question hit vectors identical.
- **The packed context is NOT byte-identical** (`packed_context_identical:
  false`). Unlike the rank-order fix, this change genuinely runs on the chat
  lane — and still costs nothing.

**Packed-item counts and per-item render sizes, all four arms.** The pack size
distribution is IDENTICAL arm-for-arm on both lanes — nothing was displaced
anywhere, which is the property the reverted patch could not have:

| lane | packed items (total / mean / p50 / max) | per-item chars mean | p50 | max |
|---|---|---:|---:|---:|
| coding before | 1760 / 9.778 / 10 / 10 | 1891.6 | 1575 | 7362 |
| coding after | 1760 / 9.778 / 10 / 10 | 1983.9 (+4.9%) | 1670 | 7395 |
| chat before | 778 / 4.371 / 4 / 9 | 5214.7 | 3504 | 23078 |
| chat after | 778 / 4.371 / 4 / 9 | 5465.2 (+4.8%) | 3626 | 23906 |

**Recommendation: default, not flag-gated.** It repairs a defect rather than
trading behaviours; its safety is structural (cannot evict, reorder, or exceed
budget, and cannot fire when the leftover is short, so the worst case is exactly
the old behaviour); the measurement agrees on both lanes; and the two cases
where bounding an item IS wanted — `pack_render_cap` and a budget-bound pack —
are already respected in the code. A flag would leave the defect on by default.

**Not a claim.** Track R's magnitude is inflated by a lexically biased bank
(question→target token coverage 0.396 vs 0.094 to a random non-target). The
DEFECT is corroborated off-bank — the pass fires on LongMemEval and costs
nothing there — the 29-question MAGNITUDE is not. No checkbox, default, cutover,
deployment, or SOTA claim moves. The parked v5-census and SWE-ContextBench pins
were NOT re-stamped and no new pin collides (`-k census`: 5 passed, 1 skipped).

Suites: `cargo test -p memphant-core --lib` 137 passed; clippy
`--all-targets --all-features -D warnings` clean; `cargo fmt --check` clean;
pytest 1027 passed / 15 skipped / 2 failed, both pre-existing at base
(`test_public_sota_claim_policy_…`, `test_spec_drift_check_…`).

Commits, none pushed: `f67f2b2a` (fix) → `d40091cc` (paired analyzers report the
render-loss target and the render-size distribution) → `91d486b4` (gitignore).
Artifacts: `docs/build-log/artifacts/track-r/track_r_phase1w_render_loss.json`
and `…/rung7-packing-reader-gate/phase1w/chat-lane-nonregression.json`
(committed, derived); per-question outputs gitignored.

## 2026-07-31 — GitHub lane coding-memory golden bank (af-w6-github)

**Preregistration first.** `docs/build-log/2026-07-31-github-lane-bar-and-privacy.md`
committed at `a7cdb876` before any extraction, per the binding gate.

**Survey verified.** All five of the owner's repo figures reproduce exactly:
Syndai 117 PRs / 0 issues / 194 review comments (179 `coderabbitai[bot]`, 15
human), Finn 194/0/0, yurivan 39/0/0, RecMe 7/16/0, eternex 1/0/0. Savida is a
subtree of Finn, not a repo.

**Verdict: three preregistered bars FAIL; no bank ships as a certified bank.**

- S1 `ci_failure_fix` (machine-authored queries, n=39): leakage **3.31×** vs the
  ≤2.05× bar — FAIL.
- P1 `public_human_review` (swe-prbench, CC-BY-4.0, n=325): **2.42×** — FAIL.
- Repo concentration: Syndai holds **90.4%** of private non-S4 goldens vs a
  ≤60% bar — FAIL.
- S2 `revert_supersession` (n=6) 1.78× PASS, S3 `fix_of_a_fix` (n=7) 1.61× PASS.
  S4 CodeRabbit (n=39) 2.11×, quarantined and never gated or blended.
- Bar-clearing slice is therefore **13 goldens**, under the ≥40 floor. The bars
  were not moved.

**S5 (private human queries) is empty, and that is the finding.** All 15 "human"
review comments are the owner replying to CodeRabbit; 11 open with
`Addressed in <sha>: …` — the actor describing his own change, the exact Track R
defect. The other 4 are rebuttals with no following change. All 16 RecMe issues
are open zero-comment backlog tickets in a repo with no CI history.

**Mis-specification recorded, not applied.** The concentration metric detects
copying, which requires the query to be writable from the target. S1's query is
emitted by CI before the fix exists and P1's by a reviewer against the pre-change
hunk, so a high ratio there is causal specificity, not contamination. Gating them
on a copying metric was a prereg error. Reported as FAIL and left standing; a
corrected instrument needs its own preregistration.

**Artifacts.** Lock `benchmarks/data/github_lane_golden.lock.json`
(`be9965cc…`), leakage `docs/build-log/artifacts/github-lane/leakage.json`
(`db2fbb4a…`). 416 goldens / 492 corpus docs, bodies gitignored and mirrored to
`~/.memphant-private/github-lane/`. 2 candidates dropped whole for secrets
(`anthropic_key`, `generic_secret_assignment`); no matched value written
anywhere. Read-only throughout (`gh api -X GET`, `git show`; no fetch, no push).
$0 spend, no model call. `--check` reproduces the lock byte-for-byte.

## W6 — convo lane: a golden bank from the owner's own agent sessions (2026-07-30, branch `af-w6-convo`)

Cost **$0**. Adjudication on subscription-model agent calls, cached by packet
`content_sha256`; no OpenRouter, no paid call, no network call on the derive
path. Nothing pushed.

**Why.** Track R's mined questions carry 0.3960 of their tokens into the target
against a 0.1008 non-target floor — 3.93× — because an LLM asked for "causally
identifying" questions satisfied it by copying identifiers out of the target. A
human's turn in a real session cannot do that: it was typed before the answer
existed. This slice tests whether the owner's own Claude Code transcripts can
supply such queries.

**The human-turn rule is provenance, not shape.** `type=user` **and**
`origin.kind=human` **and** non-sidechain **and** no `toolUseResult` **and** not
`isMeta`, then wrapper-stripped, ≥40 chars, paste-guarded. A 60-session survey
(seed 11) showed the trap: 44 of 2,718 `user` records are plain-string subagent
dispatch prompts with no origin stamp — model-authored, and admitted by any
content heuristic.

**Two defects the rule did not catch, both found by adjudication, not by survey.**

1. **A2 — the harness stamp is necessary but not sufficient.** A cross-session
   agent-to-agent message arrives as `type=user`, `origin.kind=human`,
   `promptSource=sdk`, non-sidechain, no `toolUseResult`, not `isMeta`. It
   satisfies **every** condition. 34 turns in the snapshot. Now rejected whole.
2. **The regex secret scan is not sufficient on its own.** It caught 12 turns by
   family; adjudicators flagged 16 more packets carrying pasted browser cookies,
   an account password in prose, a serialized session record with a client IP,
   and live API-key material in at least four distinct source sessions. A flag
   now quarantines every unit visible in that packet out of the bank, the
   corpus, and every shipped haystack.

> **Owner action, outside this lane:** live credential material is present in
> plaintext in transcripts under `~/.claude/projects/`. Those keys should be
> rotated. No value was written into any artifact, lock, log, or report.

**Yield.** 4,843 sessions prefiltered → 412 scanned in full (1.45 GB, frozen
snapshot `a95351ad…`) → 2,655 stamped human turns → 1,200 admitted (45.2%) →
204 candidates → **43 goldens** (21.1% of candidates), 32 sessions, 9 projects.
Dominant reject is `question_self_contained` (85): this owner writes fully
specified briefs — file, line, root cause, remedy, gate command inline — and
such a turn needs no recalled context however human-authored it is.

**Leakage, `scripts/track_r_leakage.py` unmodified (`1dd9435e…`), n=43.**

| | this bank | Track R orig | Track R para | human band |
|---|---:|---:|---:|---|
| target mean | 0.3367 | 0.3960 | 0.135 | 0.175–0.287 |
| exhaustive floor | 0.2246 | 0.1008 | 0.067 | — |
| concentration | **1.4991** | 3.93 | 2.05 | 1.76–2.03 |

**Verdict: `prereg_bar_pass: false`, and it is NOT a contamination finding.**
Per the coordinator's A3 split, provenance and lexical tractability are reported
as two fields and never collapsed: `provenance.class = human_authored_pre_answer`,
43/43, `contamination_possible: false`. Five preregistered rows fail —
target mean ≤0.25, target max ≤0.60, construct prediction ≤1.30, ≥6 per shape
(`file_symbol_grounding` 0, `state_churn` 3), skeleton ratio ≥0.90 (0.8605).

**A construction defect, found by looking where the band said to look.** The
bank sits *below* the human band on ratio but *above* it on absolute — only
possible if the floor is high. A shipped memory unit is `user turn + agent
reply`, and the reply restates the user's vocabulary on both sides of the
metric. Same pinned script, unit reduced to the user turn alone: target
**0.3367 → 0.1871**, floor 0.2246 → 0.1370, concentration 1.4991 → **1.3657** —
*inside* the human band. **Absolute-coverage bars are not portable between banks
with different unit definitions.** The shipped corpus is deliberately left as-is;
narrowing it to hit a number would be bar-fitting.

Corroborating: cross-project floor 0.1986 ≈ in-project floor 0.2246 (a question
covers ~20% of anything this owner ever wrote, in any project — house dialect,
not pointing); and concentration is length-driven (shortest third 1.766, longest
1.393), the 0.875 worst golden being a 56-char follow-up.

**Recommendation, not an action taken here:** the ≤1.50 bar in
`docs/build-log/2026-07-31-track-r-paraphrase-bar.md` §4.1 sits below the measured
human floor, and `foundry-ai/swe-prbench` — a published *human* corpus — fails it
at 2.42. It should be recalibrated by whoever owns that document.

**Strategic note.** The sibling GitHub lane's human stratum came back empty (all
15 "human" review comments are the owner replying to CodeRabbit, 11 of them
`Addressed in <sha>`). This conversation corpus is the only in-house source of
genuinely human-authored coding queries, which is why the posture here is
aggressive rejection rather than generous inclusion.

**Custody.** Bodies, corpus, spot-check, leakage report and verdict ledger are
gitignored and mirrored to `~/.memphant-private/convo-lane/` with sha256 in
§10 of the prereg. One committed lock, counts and hashes only. All three
committed artifacts re-scanned by the §5 detector: clean. Determinism:
`--check` exit 0, `bd08c93f…` reproduced. Tests: 20 passed.

## W7 — external instrument adoption: two adopted, two rejected (2026-07-30)

**Nothing here is an accuracy number.** The deliverable is "pinned, verified,
demonstrably runnable". Every count below was recounted from shipped bytes; no
card value and no badge was trusted.

**AMA-Bench — ADOPTED.** `benchmarks/manifests/ama_bench.lock.json`, HF
`AMA-bench/AMA-bench` @ `a5777378`, MIT. The licence disagreement resolves
cleanly and is not a disagreement: the dataset is MIT (card frontmatter + card
prose + a real MIT LICENSE file in the AMA-Hub code repo), and the CC-BY-4.0 is
arXiv's *submission licence for the paper*, shown as a CC-BY icon on the abs
page. Two artifacts, two licences. **MIT governs.** Shape verifies exactly:
208 episodes, 2496 QA, 2496 unique uuids, 14944 turns, and the load-bearing
slice is 36 SOFTWARE/swebench episodes x 12 = **432 QA**, 3716 turns, median 94
turns, 0 empty questions/answers/actions/observations. Human authorship is
**confirmed as a specific paper claim** — S3.3.1, "authored by graduate-level
annotators with research experience in LLM agents", "cross-review sanity check
by a second annotator" — corroborated by the absence of any LLM-calling QA
generator in the repo (the only generator is programmatic, for the TextWorld/
BabyAI synthetic subset, which is *not* on HF). What is **unverified** and
recorded as such: no guideline doc, no annotator count, no IAA for the QA
labels (the paper's agreement numbers belong to its judge-validation study —
do not misread them), no construction code, no per-item provenance.
Defects found: the card documents a `source` field that is absent from all 208
records; TEXT2SQL ships 102 wholly-null turns and OPENWORLD_QA 41 empty
observations (our SOFTWARE slice is clean, 0/3716); paper Table 7 says 34 SWE
trajectories where prose and data say 36.

**MemoryCode — ADOPTED.** `benchmarks/manifests/memorycode.lock.json`, HF
`CohereLabsCommunity/memorycode` @ `32d888b1`, Apache-2.0 from a real LICENSE
file in `Cohere-Labs-Community/MemoryCode`, not a badge. **Every claimed count
reproduced exactly**: 8400 sessions, median 12.5, max 100, **0 empty text**,
2913 instruction-update events, 255/360 instances with at least one, 4908
sessions shipping `session_regex`. Labels are synthetic and honestly described
as such; the graders are hand-written regexes, so **no LLM judge on any path**.
One schema trap worth carrying: `session_regex`/`session_eval_query` are NOT
positionally parallel to `type`/`topic` (they align in only 811 of 8400) — they
are the cumulative active-rule set at that session. Anyone zipping all four
arrays will mis-pair rules to events.

**ClawArena — PINNED, PROVEN RUNNABLE, NOT ADOPTED as the preference-lane
instrument.** `benchmarks/manifests/clawarena.lock.json`, HF `Haonian/ClawArena`
@ `146bf9a9` (the `aiming-lab` namespace 401s), licence recorded MIT per owner
direction, provenance noted verbatim, not re-litigated. Mirror is local-only;
only the lock is committed. Three findings:
- **The 337-round claim is false against shipped data.** Measured: **1891
  rounds across 65 scenarios** (1543 multi_choice / 348 exec_check). 337/12-
  scenarios comes from a stale shields.io badge and a stale README table whose
  own 95/242 split matches no measured partition. The data is *larger* than
  advertised, not smaller.
- **The checkers are NOT withheld.** 348/348 exec_check rounds carry a command,
  and 207/207 referenced `check_*.py` scripts exist on disk. One trap: the
  command lives at `exec_check.command` on 64 rounds and `eval.command` on 284.
- **The corrections are MODEL-GENERATED.** The project's own pitfalls doc says
  hil_s1 was "the first scenario whose dialogue content was batch-generated by
  a subagent". It never claims human authorship — the human reading was ours,
  and it was wrong. That is the disqualifier: the gap we are closing is the
  absence of an instrument whose labels are not LLM-mined. Compounding it, its
  native scoring is unreachable at $0 (exec_check needs a live agent writing
  files; multi_choice needs a reader) and it ships no retrieval ground truth,
  so our adapter's gold is derived and covers only 385 of 1801 rounds.

**Claw-SWE-Bench — REJECTED.** `benchmarks/manifests/claw_swe_bench.rejected.json`,
@ `ca9da741`. Best-documented of the four — real MIT LICENSE, real Gebru
datasheet, no badges — and the 350-task claim is exactly true. Rejected anyway
on two grounds: it has **no memory construct whatsoever** (single-shot patching,
"instances are independent at the task level"), and it is **100% upstream
reuse** — 350/350 ids are literal SWE-bench ids, byte-identical to
SWE-bench_Multilingual (300) + Verified (50) on every content column, 0/700
mismatches on the test lists. Adopting it would add total contamination surface
to our coding lane and zero signal. Its one novel artifact is `lite80_ids.json`.

**Adapter.** One script, `scripts/external_instrument_adapter.py`, not a harness
per benchmark. An instrument contributes a loader yielding ingest units plus
probes with deterministic gold ids and inherits the shared scratch-DB / server /
worker / recall runtime. Scoring is by `citation_episode_id`, not substring:
recall returns *citation windows*, so any tag-in-body scheme undercounts exactly
the long units this lane cares about — and the id path lets source text be
ingested verbatim with no marker injected. The runner verifies the pinned
sha256 (or, for a tree-shaped source, an aggregate tree hash) before minting a
database, so a mutating mirror fails closed rather than scoring.

**End-to-end proof, all $0, no reader, no judge, no paid call:**

| instrument | groups | units | probes | result |
|---|---|---|---|---|
| ama_bench | 4 | 401 | 29 | ran clean, hit@k 0.103 |
| memorycode | 25 | 749 | 26 | ran clean, hit@k 1.000, hit@1 0.423 |
| clawarena | 6 | 91 | 72 | ran clean, hit@k 0.472 |

**Do not read these as scores** — n is 26–72 and the slices are the first N
groups, not a sample. One observation is worth flagging as a hypothesis for a
powered run, not as a result: on the MemoryCode smoke the **superseded** session
outranked the current one on **15 of 26** correction-retention probes. That is
the exact failure mode the preference lane exists to detect, and it is now
measurable for $0.

**n, resolvable effect, full-run cost (bytes measured, never estimated):**

| instrument | n | paired effect resolvable @0.05/0.80 | full retrieval run |
|---|---|---|---|
| AMA-Bench SOFTWARE | 245 step-anchored probes / 36 episodes | 6.4pp (cluster-adjust to ~13pp on 36 clusters) | $0, 8,884,365 B, 3716 units, 245 recalls |
| MemoryCode | 1063 probes / 257 instances (3616 current-vs-stale pairs) | 3.1pp (cluster-adjust ~6pp) | $0, 20,444,840 B, 8147 units, 1063 recalls |
| ClawArena (derived gold) | 385 probes / 59 scenarios | 5.1pp (cluster-adjust ~10pp) | $0, 6,436,902 B, 720 units, 385 recalls |

AMA-Bench's other 187 SOFTWARE questions need a reader and are out of scope at
$0; its judge is also a live confound — the repo's own README reports Qwen3-32B
is lenient worst-case precisely on swebench (100 lenient vs 15 strict against
Claude-4.6) and absolute scores swing 0.33–0.49 by judge. Pin the judge or do
not compare. ClawArena's native run is an agent-hours cost, not a token cost,
and is deliberately left unpriced rather than guessed.

**Instrumentation bug found and fixed en route.** `gate_runtime.drain_worker`
trusted the worker's self-report on the `MEMPHANT_WORKER_DRAIN` path. Measured
on 401 queued `reflect_episode` jobs: the worker printed `drain completed=256`
and **exited with 145 still queued**; a second invocation finished them, a third
reported 0. The tick path already refuses to trust the self-report and asks the
database — the drain path did not, so **any bench on that path could score
against a partially compiled corpus and never know**. Now re-invokes until the
database says the queue is empty and fails closed on a no-progress invocation.
Worth an audit of past runs on this path.

**Reproduce:**
```
docker start memphant-postgres-1
cargo build --release -p memphant-server -p memphant-worker -p memphant-cli
python3 scripts/external_instrument_adapter.py --instrument ama_bench \
  --source ~/.memphant-private/w7-instruments/ama-bench/test/open_end_qa_set.jsonl \
  --out <out>/ama.json --limit-groups 4 --port 39473
python3 scripts/external_instrument_adapter.py --instrument clawarena \
  --source ~/.memphant-private/w7-instruments/clawarena/data/clawarena \
  --out <out>/claw.json --limit-groups 6 --port 39476
# memorycode needs pyarrow; any venv python with it works:
<venv>/bin/python scripts/external_instrument_adapter.py --instrument memorycode \
  --source ~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet \
  --out <out>/mc.json --limit-groups 25 --port 39475
```
Drop `--limit-groups` for a full run. Each run re-execs itself through
`scripts/with_scratch_db.sh` onto a fresh migrated scratch DB, drains the worker
before any query, and drops the DB on exit even if killed.
## 2026-07-31 — Instrument Register (af-w7-register)

**The gate on all further spend.** $0 audit, no paid model call. Register:
`docs/build-log/2026-07-31-instrument-register.md`; machine-readable
`benchmarks/manifests/instrument_register.json` (18 lanes, 7 substrates, 9 findings,
7 stop conditions) and `benchmarks/manifests/instrument_power.json`, generated by
`scripts/instrument_power.py`.

**Power is now computed, not asserted.** Two-sided exact conditional-binomial McNemar at
α=0.05, integrated unconditionally over N_d ~ Binomial(n, ψ). Every ψ is measured from a
banked artifact named in `psi_source`, or the lane is recorded as having no paired run.
Validated: returns 0.0319 ≤ α at δ=0. The standing convention it replaces —
`derive_phase2_packet.py`'s `"~80% at psi~=0.15"` — had no run behind it.

**Do not launch Phase 2 as specified.** `d_min = 7pt` on n=221 gives **72.8%** power at the
assumed ψ=0.15, 68.0% at the 80q pool median (0.167), and **54.2% at the ψ observed on that
very lane** (0.229). True MDE 7.6–9.2pt; 7pt needs n=260–390, and n=390 is reachable only by
consuming the sealed-259 confirmation inside the screen. The $142.32 ceiling itself is sound
and reproduces byte-identically.

**Structural floor: n_d ≥ 6.** Below six discordant pairs the exact test has no rejection
region, so power is zero at any effect size. Generalises the n≤12 tripwire reclassification
and newly retires `coding_events_golden` (n=40, one repo, held-out 4), all five MemSyco
calibration splits (n=12–14), and the b=c=0 non-regression pairs as evidence of equivalence.
The `_abs` sentinel that killed `pack_render_cap` is confirmed dead structurally: n=12,
b=0, c=2, exact p=0.50.

**STATE-Bench would have been a void purchase.** Nothing was ever materialised locally, so
the lock's task counts were unbacked transcription; a fresh $0 clone verifies them and the
MIT licence. But the adapter POSTs `tenant_id` and omits three required fields against
`deny_unknown_fields` — reproduced live through the official loader: HTTP 400 on every
retrieval call. With 3 retries and no trajectory saved, a run bills **$211–634 for zero
scored rows**. Fix is one line out, three lines in at `memphant_memory_agent.py:33`.
Working-run ceiling derived: $2,253–$10,704.

**Three adapters have now failed at first contact on our side** — STATE-Bench, STALE
(timezone-naive timestamp), MemSyco (extractor rejects on evidence grounding); two only
after money was authorized. Recommended gate: no paid authorization for a lane whose adapter
has not completed a $0 stub-server round trip against the current strict contract since the
last contract change.

**Verdicts.** SOUND: Track R paraphrase bank (unrun), Syndai docs gate, ForgetEval, Track U
(unrun), and the Postgres/RLS/MCP/store-divergence substrate instruments. DEGRADED:
LongMemEval-S, Phase-2 packet, GitHub lane, Memora, MemSyco, file plane B2/B3, HTTP-boundary
SLO. BROKEN: LME-V2, Track R original, `coding_events_golden`, SWE-ContextBench, SWE-Explore,
STATE-Bench, STALE, the `_abs` sentinel. ABSENT: procedural (n=1, CI [1.0,1.0]), hot/cold
planes (no instrument **and no feature**), file-plane store divergence.

**Corrections to standing beliefs.** (1) LongMemEval is **not** deprecated upstream — the
only such strings are our own filename and `2026-07-31-w2-reader-composition-prereg.md:51`,
which is blocking W2.1 for no reason. (2) The served-path superuser/RLS caveat is stale;
`served_path_rls.rs` catches it, negative control included. (3) The `claim_reflect_jobs`
race is fixed — a blocking `pg_advisory_xact_lock` ships at
`20260724_003_worker_claim_throughput.sql:128`. (4) Memora's "flat 43/71 vs 44/71" hides
**25 discordant cells out of 71**. (5) `hit@10` is dead on the LME slice: max
`first_answer_rank` is 5, so @10 ≡ @5 on all 166 rows.

**The paraphrase bank should be certified.** 19/20 checks pass; the one failure,
`leak_concentration_le_1_50` at 2.018×, sits below the measured achievable floor of 1.79×
(W0 probe, n=27) and inside the independent human band 1.76–2.03×. It strictly dominates the
bank we actually ran (distractors 180/180 vs 75/180; lexical overlap 0.0162 vs 0.0517). One
bar currently has two values in force: 1.50× here, 2.05× in the GitHub-lane prereg.

**Spend plan.** Nine $0 prerequisites first (Z1–Z9), several of which change what a paid run
is worth — chief among them running the paraphrase bank (free; preconditions verified) and
re-emitting the code-lane evidence rows, without which the Phase 3 ceiling is **not
derivable** (its 1,440-call budget is). Paid ranking: Phase 3 on a decontaminated bank is
the only paid run with a clean decision behind it; Phase 2 is priceable but under-resolved;
STATE-Bench must not be purchased today at any price.

**Not a claim.** No checkbox, default, cutover, deployment or SOTA claim moves. Nothing was
run for money. Commits `7a3faa68`…`d1c11bec` on `af-w7-register`, none pushed.

**Audit hazard recorded:** 87 of 88 entries in `canonical-artifact-allowlist.txt` are absent
from this worktree; Memora and MemSyco evidence survives only in `/Users/sidsharma/Memphant`.
An audit run inside a worktree will wrongly report "never run" for lanes that did run.

---

## 2026-08-01 — Preference lane scored for the first time. Negative, and diagnosed. ($0)

The preference / user-learning lane had **never had a number**. It has one now:
on **1063 MemoryCode supersession probes over 257 instances**, MemPhant's
**latest-state-wins rate is 0.3123** [0.2846, 0.3414] against a BM25 lexical
control's **0.3198** [0.2901, 0.3502]. **ΔLSW = −0.0075, cluster-bootstrap 95%
CI [−0.0370, +0.0228]** (10,000 resamples over instances, seed 20260801);
cluster-permutation p = 0.657, exact McNemar p = 0.674 (134 / 142 discordant).
**By the preregistered rule this is a NEGATIVE: MemPhant does not beat a lexical
baseline at telling a live rule from a dead one.**

Both directions, by name, with the third bucket so suppression cannot pass as
application: **Misapplication Rate 0.6717 vs 0.6736** (Δ −0.0019, CI [−0.0318,
+0.0288]); Appropriate Application = LSW above; neither-returned 0.0103 vs
0.0019 — the **only** significant difference, and it runs *against* MemPhant
(Δ +0.85pp, perm p = 0.029). Descriptively MemPhant is also worse at surfacing
the current rule at all: **hit@10 0.786 vs 0.933**. The adoption smoke's
MR = 15/26 = 0.577 **understated** the failure; at n = 1063 it is 0.672.

**Diagnosis, from the run's own DB before it was dropped.** `memory_edge` by
kind: **empty — zero edges of any kind**. `memory_unit` by state:
**`active` = 8147, all of them**. By kind: **`episodic` = 8147**. Units with a
`predicate`: **0**. `retention_tier`: **`hot` = 8147**.

**Supersession is not broken — it is unreachable.** The retain payload carries no
subject/predicate (correctly; they are compiler hints); `compile_job`
(`service.rs:5305`) emits one `Episodic` candidate with `fact_key: None` and no
subject, fact extraction being default-OFF and no structured provider at $0;
`has_explicit_subject` (`lib.rs:12222`) is therefore false; and the whole
supersession branch (`lib.rs:11670`) is gated on it under its own comment
"AUTO-KEYS NEVER SUPERSEDE". Second lock: that branch also requires
`kind == Semantic`, and these are `Episodic`. So the `UnitState::Superseded`
recall exclusion is **correct and dead** — nothing ever reaches that state.

**And no fallback recency signal exists.** `temporal_score` (`lib.rs:10896`)
fires only on a literal `current`/`latest`/`now` query token AND a
`Semantic`+`Active` unit → **0.0 on all 8147 units for all 1063 probes**.
`days_since_last_review` (`lib.rs:11062`) returns a **constant 14.0** with zero
review events, so decay is an order-preserving global scalar and contributes
**exactly nothing** — decay keys on review events, never on wall-clock age, even
though `observed_at` was stored correctly in true chronological order. The modal
failure is the stale session at rank 0 with the current one at rank 1: both
retrieved, wrong one first. Not a recall failure — the total absence of a
live/dead distinction.

**Implications.** The bottleneck is the **write path**, not the read path: until
something mints an explicit subject key for a restated convention, no reranker,
budget, or pool depth can separate the two, because the units are the same kind,
state, tier and decay and differ only in text. This is direct evidence for
`04` §13.2a's own call that a `preference`'s recall path is **assembly, not
ranking** — competitive retrieval on this construct is statistically
indistinguishable from BM25. On the hot/cold plane, `retention_tier` is
confirmed inert (`hot` × 8147), but scoped honestly: demotion moves storage, it
does not retire a rule, so **this run gives no evidence that tiering would move
latest-state-wins**. It is a cost story here, not an accuracy story.

**Instrumentation.** Merged `accuracy-first` mid-task after finding this worktree
had `20260730_004` (worker pool under FORCE RLS → queue-wide count matched zero
rows → drain reported one batch as complete) **without** the `005`
security-definer fix. A partial Arm A was **killed and its artifacts deleted**,
not reconciled; both arms re-ran from scratch. Added `verify_corpus_compiled` to
the adapter: asserts on the bench superuser credential — never a worker
self-report — 0 pending, 0 failed/dead, episode count matches retain, and **every
episode minted at least one unit**. It passed 8147/8147/8147.

**Prereg `968d7fba` committed before any measurement.** Report:
`docs/build-log/2026-08-01-preference-lane-first-measurement.md`. Artifacts under
`docs/build-log/artifacts/2026-08-01-preference-lane/`. Commits `968d7fba`,
`3eedb5c5`, `bdeeb834` on `af-w8-memcode`, **none pushed**. Track U's 51 goldens
remain unmeasured. `paid_model_calls: 0`. No checkbox, default, cutover or SOTA
claim moves.
## W8 — silent worker under-drain: the two remaining exposed paths (2026-07-30, `af-w8-drain`)

**Headline: no banked number moves.** Both audited results survive, and both now
carry live re-run evidence rather than an argument. The one thing that did move
is a latency number nobody asked about, flagged at the bottom and NOT attributed
to this bug.

**Root cause is the sibling session's, not Python's.** `20260730_004` made the
worker pool assume `memphant_worker`, so FORCE RLS applied to the worker's
queue-wide, tenant-unbound drain-exit count; it answered 0 at any queue depth and
`MEMPHANT_WORKER_DRAIN=1` exited after one tick. Fixed at `a47a4a40` +
`20260730_005_pending_worker_job_count.sql`. This branch merged that first
(`1bddcda6`) and everything below runs on top of it.

**Caller reading re-verified against the real mechanism.** Exposure needs a pool
under a capability role AND no independent bench-credential check. `psql`-based
verification runs as the scratch-DB superuser, so it is never blinded.
- SAFE, independent DB check: `code_lane_run_memphant.py` (`compilation_summary`
  asserts `pending_jobs=0`, `dead_jobs=0`, `done_jobs=expected`);
  `generate_memora_memphant_answers.py` (structured ledger ⇒ the tick path, which
  always asked `psql`).
- SAFE, count-gated — **owner's reading was conservative here**: an under-drain
  reports a short `completed=`, so `run_swe_contextbench_memphant.py`
  (`completed == 24`), `generate_stale_memphant_answers.py` (`>= sum(ingested)`)
  and `external_instrument_adapter.py` (`compiled == ingested - deduped`) all fail
  closed already.
- GENUINELY EXPOSED, both now fixed: `episodic_lane_run_memphant.py` (no check of
  any kind) and `gate_run_memphant.py`, which keeps a **private copy** of
  `drain_worker` that `06b0a44b` never touched and whose caller only prints the
  count. Both gained `gate_runtime.assert_worker_queue_empty`.

**C1 episodic slice — CLEARED, on three independent lines.** (1) Immune by
construction at the time: at `6d01789b` (2026-07-22) `PgStore::connect_worker`
issued no `SET ROLE` — no `PoolSpec` existed — so the RLS-blinded count could not
reach it; `004` landed 2026-07-30 16:09, eight days later. (2) The banked
`compiled_jobs` are exact identities with the enqueued job count (synthetic
227+10+1 = **238**; real prod 35+235+1 = **271**), which an under-drain cannot
produce; now pinned by test. (3) **Live re-run** of the deterministic synthetic
corpus: `compiled=238`, Bar 2 passes on both tenants, `correctly_excluded` 13 and
12, 0 leaks, tenant ids identical to the artifact — every correctness number
reproduces exactly. The real-prod corpus is a gitignored one-time extract, no
longer on disk, so that arm rests on (1) and (2).

**`bench_lme` chat lane — never exposed to the root cause, but vulnerable to a
second mechanism, now proven and fixed.** It connects via `PgStore::connect`
(`connect_with_capabilities` ⇒ `PoolSpec::unrestricted`, no `SET ROLE`) and always
has — one commit ever touched that line — and the banked baseline's own recorded
command runs against `postgres://memphant:memphant@…`, the scratch superuser. RLS
never applied. **But** `while run_worker_tick(usize::MAX) > 0 {}` is unsound for a
different reason: a failed job is released with `retry_backoff_seconds` and
`claim_reflect_jobs` excludes `run_after > now()`, so the next tick completes zero
with the haystack still partly compiled. Proven live, not argued:
`crates/memphant-store-postgres/tests/drain_backoff_hides_pending_work.rs` claims a
real job, releases it with backoff, and shows the next claim empty while
`pending_worker_job_count() >= 1`. It has to be a Postgres test —
`InMemoryStore::release_reflect_job` ignores `retry_after_seconds` outright, so the
in-memory store cannot express the bug (the store-divergence anti-pattern again).

**The chat lane's load-bearing number re-derived from scratch.** The 0.6145 rung-7
baseline was re-run end to end on the fixed, DB-verified drain (178 questions,
seed 20260713, bge-small, fresh scratch DB, $0): **r@5 = r@10 = 0.6144578313253012,
n_scored 166 — byte-identical to `dev-fast-retrieval.json`, with ZERO
per-question differences across all 178** and no degraded reads. The banked
`--disable rerank` arm no longer exists (retired at `33586946`), which is why the
re-run drops the flag: that arm is now the only behaviour.

**Fix shape.** One mechanism, not two: `drain_finished` moved from the worker
binary into `memphant-core::service`, and `memphant-worker`'s drain mode and
`bench_lme` now exit on the same database-confirmed condition; the bench also fails
closed when a tick makes no progress with work pending. Python side, one shared
`gate_runtime.assert_worker_queue_empty` counting `queued|running|dead` on the
bench credential.

**Suites.** pytest 1052 passed / 15 skipped, 1 failed: `test_public_launch_gate`
(`playwright: command not found`, environmental, red at base). Rust
`--all-targets --all-features --no-fail-fast`: 2 failed —
`contextual_chunk_write::recall_chunk_renders_matched_window_plus_neighbour`
(red at `06b0a44b`, verified by checkout) and `syndai_trace_compare` (expected,
sibling-owned). `cargo fmt --check`, `clippy -D warnings`, `cargo test --doc`
clean. Live-PG `--ignored` leg: the new drain test passes; the combined
`-p memphant-store-postgres -p memphant-worker` invocation shows 3 failures
(`retain_resource_registers_and_enqueues`, `the_worker_pool_counts_queued_jobs_
across_every_tenant`, `worker_drain_exits_zero_and_prints_exactly_one_summary_line`)
that reproduce identically at `1bddcda6` and pass when each binary gets its own
scratch DB — shared-scratch-DB cross-contamination between test binaries, not a
code fault. `hot_path_slo_pg` failed only under bench contention and passes alone.
Also repaired three fixtures already red on trunk: the `06b0a44b` drain test's
`kwargs["env"]` KeyError and two `apply_memphant_migrations` counts stale at four
migrations since `005`.

**One number DID move, and it is not this bug.** C1 Bar 1 (HTTP-boundary recall
SLO, 200 calls, same script, same corpus) measured **p50 81.8 ms / p95 108.6 ms**
today against the banked **32.6 / 37.2**. It still passes the 200/500 ms bar, and
three measurements under falling machine load (149/221 → 116/166 → 82/109) say
part of the gap is contention — but not obviously all of it. Correctness is
untouched. Nine days of recall-path change sit between the two runs; nobody has
looked, and this audit did not.

Commits `eda37a7b` `96115a76` `7be37945` `cb95502d` on `af-w8-drain`. Not pushed.
## Ranking — the Exact channel carries its own magnitude (2026-07-30, branch `af-packadj`)

**Adjudicates a regression we introduced.** `03fa1266` (the packing rank-order
fix, merged `e99b912d`) broke
`cargo test -p memphant-eval --test syndai_trace_compare`. Verified by checkout,
not inference: at `a96c289c` the full Rust workspace is **664 passed, 0 failed**
and `syndai_coding_continuity_fixture_families_pass` is `ok`. A prior agent
called it pre-existing; that was true only relative to a base that already
contained `03fa1266`.

**(a) It is a ranking defect, not a packing one.** Instrumented inside
`pack_recall_context`. Fused order was `chatter_0` 0.0550276, `chatter_1`
0.0541468, `task_state` 0.0539235, `error_budget` 0.0535178 — both real answers
last, by 0.4% and 0.8%. Mechanism: weighted RRF is rank-only, so the Exact
channel's decisive 1.000-vs-0.333 subject-key margin collapses to one rank
position (~0.0005), while the lexical family (`Lexical` 1.0 + `Semantic` 2.0 —
the latter is `token_set_overlap_score`, a body scorer despite the name) votes
with 3× Exact's weight off exactly the text keyword stuffing inflates. Proof it
is ranking: `packing_relevance_score − exact_score` reproduces the fused order
exactly (1.92052 / 1.91964 / 1.91942 / 1.37376). **`exact_score` was the entire
content of the eviction contest `03fa1266` deleted** — one channel's magnitude
re-applied late, at pack time, by a formula nobody opted into. That commit was
right about the mechanism and wrong that nothing was being defended.

**(b) Better lexical scoring does not dissolve it — confirmed, as predicted.**
Under `bm25-control` the real answer and both distractors tie **bit-for-bit** at
2.64491128921508789 and the order comes off the alphabetical body tie-break;
under `bm25-code` the real answer scores marginally *lower* (2.69619059 vs
2.69619083). Under `Overlap` both passes are exact three-way ties. Fused scores
are **bit-identical across all three scorers**. IDF and length normalisation
cannot discriminate here.

**(c) Resolution — `3fc4eede`, one expression.** The Exact contribution is
scaled by its own score: `exact_score` is a calibrated 0..1 coverage fraction of
a curated `fact_key`, unlike the other channels' incommensurable scores. Fused
becomes 0.0539235 / 0.0535178 / 0.0460765 / 0.0453355 — both answers first by
16% instead of 0.4%. Within-channel order provably unchanged; `03fa1266`'s
contract untouched (the pack still fills k in rank order). Two alternatives were
**executed and rejected**, not argued away: `EXACT_CHANNEL_WEIGHT` 1.0→3.0 leaves
`missing=["mem_rollout_task_state"]`; an `exact_score` channel tie-break leaves
`missing=["mem_error_budget_constraint"]`. Each recovers a different single
answer; only the magnitude recovers both.

**(d) Both measurements, against current trunk.** Rebaselined onto the
render-loss completion pass; measurement base `f67f2b2a`, the commit that
produced 168/180, both arms differing only in the one expression.

- **Track R (bm25-code, embeddings off, k=10):** base **168/180** (r@5 0.9222,
  r@10 0.9333) — reproducing the committed render-loss run to the digit — and
  fix **168/180**. Paired exact McNemar **0 gains / 0 losses, p = 1.0** at both
  k. Fused top-10 ceiling 173 in both. Every statistic identical, including
  `packed_item_chars_total` 3,491,737. Worker fully drained in both arms
  (`done_jobs=64056`, `pending_jobs=0`, `dead_jobs=0`).
- **LME-S (n=178, seed 20260710):** 0.6144578313253012 in both arms, 0 flips on
  166 scored, p = 1.0, and `packed_context_identical: true` — byte-identical
  packed context on all 178 questions.
- `syndai_trace_compare`: **2 passed, 0 failed.** New ranking-stage guard
  `keyword_stuffed_body_does_not_outrank_a_fully_covered_subject_key` verified
  red at `3fc4eede^`, green after.

**The nulls are mechanism, not luck.** Both banks ingest raw episodes, whose
units reach `derive_fact_key` with no subject or predicate and get
`{scope_uuid}:auto:{hash}`. No query token matches a UUID fragment, so
`exact_score == 0`, so no candidate on either bank ever enters the Exact channel
and the changed expression is never evaluated. **Corollary worth stating: both
instruments are blind to this channel, so neither could have detected a
regression in it either.** This is a correctness repair with a proven-null blast
radius — *not* an accuracy win, and it must not be counted as one. A
`retain`-shaped corpus with real `fact_key`s is not measured by anything this
program owns.

**(e) Coverage-gap audit — the deliverable.** The packing fix recorded
`cargo test -p memphant-core --lib`, which runs one crate's *unit* tests and
excludes all 30 files in `crates/memphant-core/tests/`, all of
`crates/memphant-eval/tests/`, and every other crate. The fixture lives in
`memphant-eval/tests/syndai_trace_compare.rs` and was never executed. The W3.3
work did run `--workspace`, saw the failure, and dismissed it as "pre-existing"
against a base that already contained the defect. Suites exercising
packing/ranking that neither ran, all free and database-free:
`syndai_trace_compare.rs`; `eval_contract.rs` (the 12 oracle goldens, two of
them packing-abstention, plus the rung4/5/6/7/10/11/12/15 lever deltas —
rung7 is a packing delta); `profile_contract.rs`; `recall_trace_golden.rs` (14
end-to-end recall tests, three of them packing); `candidate_pool.rs`;
`recall_pool_depth.rs`; `cross_reranker.rs`; `quantity_rollup.rs`;
`contextual_chunk_write.rs`; `chunk_span_invariant_repro.rs`;
`temporal_grounding.rs`; `embedding_channel.rs`; `bitemporal_recall.rs`.
**Standard from now on:** (1) `cargo test --workspace --no-fail-fast` is the
floor, and any narrower invocation must be justified in the build log; (2)
attribute every failure to the commit that introduced it via bisect against
trunk — never write "pre-existing" without naming a revision predating the
program's own work; (3) packing/ranking changes additionally run Track R and
LME-S; (4) the Postgres `--ignored` leg for store/roles/worker changes.

**The audit found two more red tests nobody caught, on two other branches.**
`recall_chunk_renders_matched_window_plus_neighbour` is red on `accuracy-first`
tip; bisected by checkout to `f67f2b2a` (the render-loss fix) — `ok` at
`ccaa9e1c`. And `test_wsa_migration_contract` ×2 plus
`test_gate_runtime::test_drain_worker_uses_one_binary_drain_without_structured_provider`
are red at `1bddcda6`, from migration `005`. So `syndai_trace_compare` was not
"the only red test on the integration branch" — same shape of miss, three
independent branches. None is fixed here; each belongs to its owner.

**Independently found and since fixed upstream:** the W3.3 `SET ROLE` made
`pending_worker_job_count`'s unscoped `select count(*) from memphant.job_state`
return 0 under FORCE RLS (`current_tenant_id()` is NULL on a pool session),
while `claim_reflect_jobs` is `SECURITY DEFINER` and still claimed — so
`MEMPHANT_WORKER_DRAIN=1` exited after exactly one batch. Reproduced here as
`compiled job count mismatch: 256 != 21370`, proven by direct catalog query, and
fixed upstream by `20260730_005` (merged in). The drain test only ever drained an
**empty** queue, which is why it passed throughout. Standing consequence:
`ccaa9e1c → f67f2b2a` and this work both measured Track R from a pre-W3.3
lineage, so **the coding lane has never been measured on a W3.3-containing
build**; 168/180 included.

**Verification.** `cargo fmt --all --check` clean; `cargo clippy --workspace
--all-targets` clean; `cargo test --workspace --no-fail-fast` 676 passed, 1
failed, 95 ignored; `python3 -m pytest tests/ -q` 1045 passed, 4 failed, 15
skipped. The single Rust failure is `f67f2b2a`'s, and all four Python failures
are a strict subset of the six on `accuracy-first` tip measured in the same
session — one of them (`test_public_launch_gate`, `playwright: command not
found`) fails identically at `a96c289c`. **No failure on this branch is
attributable to this change.** The parked v5 census skips on its own and was
**not** re-pinned; the terminal SWE-ContextBench rehearsal was not touched.

Full record: `docs/build-log/2026-07-30-exact-channel-magnitude.md`.
## 2026-07-31 — W0 instrument validity (BLOCKING) — COMPLETE, $0

Branch `af-w0-instrument`, worktree `/Users/sidsharma/Memphant-af-w0-instrument`.
Nothing pushed. No checkbox, default, cutover or SOTA claim moves. Ownership
question (d) is **not** decided here.

### W0.1 — the paraphrase bank

Preregistered first (`e5fda0de`), mined after. `scripts/track_r_leakage.py`
reproduces the program spec's §1 reference to the digit on the original bank
(0.3960 / 0.3880 target, 0.0945 sampled non-target, 4.19×, 105/180 narrowing to
one) and adds a seed-free exhaustive floor of 0.1008 → 3.9286×.

Bank `4aed8e99…`, 180 goldens, 60/60/60, accept rate 0.7895, `--verify-lock`
byte-identical, 839 cached subscription-agent replies, $0.

**`bar_passed: false` — 20 of 21 checks pass; the headline leakage criterion
fails.** Unit = one content event, floor = same-attempt hard negatives.

| measure | original | paraphrase | bar |
|---|---:|---:|---|
| q→target coverage mean | 0.3960 | 0.1346 | ≤0.25 PASS |
| non-target exhaustive floor | 0.1008 | 0.0667 | — |
| concentration | 3.9286 | **2.0180** | ≤1.50 **FAIL** |
| excess over floor reduced | — | 77.0% | ≥0.75 PASS |

The bar was **not** moved. Two independent lines say ≤1.50 was below the
achievable floor: the floor probe (`23da53ac`) measures max-abstraction questions
that survive the uniqueness gate at **1.790** (n=27), and owner-supplied
human-corpus calibration puts human coding queries at **1.76–2.03×** on
same-domain negatives. **Mis-specified bar, not a bad bank** — recorded as a
finding, not a re-preregistration.

Counter-evidence recorded too: absolute coverage 0.1346 sits **below** the
0.175–0.287 human range, so the bank **overshot**. The withholding gate bans every
identifier surface; engineers name files. The two banks bracket reality
(0.1346 < human < 0.396), making every W0.2 margin a **lower bound**.

**W0.4 resolved:** distractor-coverage floor raised 50% → **100%** (achieved
180/180, 900 verdicts). The old floor was a concession to a rare-token selector
that returned an empty non-target set 105/180 times, not a judgement about the
construct.

### W0.2 — five arms, two stages — DIAGNOSTIC, NOT PROMOTION-GRADE

Control r@5 0.1167 / r@10 0.2556 (BM25 falls **0.8944 → 0.2556**, landing near
third-party CLARC's R@10 ≈ 18.06 on genuine NL→code queries — the strongest
evidence the confound was in the instrument).

| arm | fused@10 | vs control | packed@10 |
|---|---:|---|---:|
| overlap / off | 0.2222 | +17/−23, p=0.43 | 0.1722 |
| bm25-code / off | 0.4944 | +48/−5, **p=7.1e-10** | 0.3722 |
| overlap / small | 0.4611 | +51/−14, **p=4.5e-06** | 0.3333 |
| bm25-code / small | **0.6278** | +71/−4, **p=6.8e-17** | **0.4889** |

**Survival: the win grew.** Margin over control at fused@10 +0.0667 → +0.2389,
ratio **3.58**. At packed the sign flipped in our favour: −0.1222 → +0.1167 (the
negative ratio is a sign flip, not a shrinkage).

- **Prediction (a) FALSIFIED** — the bm25-code advantage did not shrink. It still
  beats overlap +52/−3 (p=1.5e-12) on a bank whose questions carry no identifier
  from their target, so the gain is not identifier matching.
- **Prediction (b) CONFIRMED** — dense flips null → strongly positive (+46/−3 on
  overlap, +29/−5 on bm25-code). **"The best configuration uses no embeddings at
  all" was an artifact of the old bank.** No default moves; it must be re-decided.

### W0.3 — LongMemEval cleaned split

Pinned by revision with a `--verify-lock`. **Standing R@k does not move:** 0.6170
cleaned vs 0.6277 deprecated on the identical 100 questions, exact McNemar
**p = 1.0**, one question. Cause: the cleaning is **de-padding** — 1,243 sessions
removed of which 1,230 are empty, total turns −0.07%, all 23,854 retained
sessions byte-identical. Scope correction: the rung-7/A1 dev cohort was **already**
on the cleaned split; only the 2026-07-10/11 wave was on the deprecated one.

### W0.5 — NOT done

The 15-golden spot-check on the original bank remains
`emitted_pending_owner_review`, and so does the paraphrase bank's. **No number
from either bank is publishable until the owner reviews them.**

### Commits

`e5fda0de` prereg bar · `57fba790` miner+tests · `c659574d` comparator ·
`23da53ac` floor probe · `b3d6518a` cleaned-split pin · `98c2e8be` cleaned-split
measurement · `cc712a86` bank lock + floor result · `2c8c1049` bar mis-specified
· `742e2e6a` five-arm comparator · `3e2cc2ba` W0.2 result.

Custody: bank, spot-check, caches, run outputs and authored brief mirrored to
`~/.memphant-private/track-r-paraphrase/` with sha256s in
`docs/build-log/2026-07-31-track-r-paraphrase-bar.md` §8.5.

## W9 — C1 replication: bars re-proven, paired probe structurally impossible (2026-07-30)

Full write-up: `docs/build-log/2026-07-30-c1-replication.md`.
Privacy prereg: `docs/build-log/2026-07-30-c1-replication-privacy-prereg.md`.

**Ownership condition (d) cannot be settled on C1.** The plan asks for "a paired
win replicated on the C1 slice", but C1 has no goldens and cannot acquire any.
Production holds **44 recall-visible episodes across 5 tenants**; recall is
tenant-scoped, so four pools are 9/6/4/1 — smaller than `k`, making r@10
identically 1.0 for both arms. Independently fatal: the leak-free anchoring rule
(anchor on a genuine human turn) has **no edge to anchor to** —
`episodic_memories.run_message_id` is 0/321 non-null, `run_messages.agent_run_id`
is 0/191 non-null, and the mission-level join yields 0 rows. All 191 `user`
rows also carry an identical provenance stamp, so canary/dogfood automation is
indistinguishable from a human, and only 63 of the 191 bodies are distinct. Max
discriminating bank = 24 items in one tenant; exact McNemar needs 6 discordant
pairs sweeping one way. The probe was not run: running it would produce a number
with the shape of evidence and none of the content.

**Recommendation: amend condition (d) to name the convo lane** (which already
implements the human-turn rule on a corpus with real provenance and real volume)
and record C1 as correctness-only, permanently rather than pending.

**Extraction.** Prereg committed before the data was touched. Read-only
(`default_transaction_read_only=on`, `SELECT` only, `embedding`/`metadata`/
`summary` refused), snapshot-pinned and hashed, bodies gitignored + mirrored to
`~/.memphant-private/c1/`, counts-and-hashes lock committed. 321 rows / 5
tenants / 44 recall-visible / 277 rolled-up; **0 secret-scan drops**; every count
matches the prereg's pinned recon.

**Bar 2 (hard gate): PASS.** State-filter EXACT, **0 leaks on all 5 tenants**,
`correctly_excluded` 222/55/0/0/0, identical across two independent runs.
Drain contract verified from the database on the bench credential
(`compiled=322 == enqueued=322`, queue empty). **Bar 3: PASS**, unchanged.

**Bar 1: the SLO regression is contention, not drift — resolved.** The synthetic
corpus, unchanged since it banked p50 = 32.6 ms, reads **p50 = 213.2 ms** today
on the same 12-CPU host at loadavg ~150. Prod reads 284.9 / 247.9 at loadavg
134 / 192. A 6.5× inflation on an unchanged corpus rules out a real 2.5×–8×
regression; it does not yield a clean absolute number. A quiet-window
re-measurement is scripted and pending — the host never fell below load 180.
The runner now records `loadavg`/`cpu_count` into the provenance and writes
artifacts before asserting the bar.

**Verification.** `cargo test --workspace` has one failure,
`contextual_chunk_write::recall_chunk_renders_matched_window_plus_neighbour`,
bisect-attributed to **`f67f2b2a`** (pre-existing; this branch touches no Rust).
`pytest` has one environmental failure (`web/node_modules` absent, Playwright
not installed). $0 spend; production read-only throughout.

### Commits

`f126286c` privacy prereg · `03aec3ce` extract mechanism + lock ·
`bf23c3bd` Bars 1–3 + loadavg-recording runner + redaction test.

### W9 addendum — Bar 1 closed by control; the zero-FK finding is a product gap

Bar 1 is recorded **resolved-by-control**, not pending: the synthetic corpus,
unchanged since it banked p50 = 32.6 ms, reads **213.2 ms** on the same 12-CPU
host at loadavg ~150. Prod reads 284.9 ms at loadavg 134 and 247.9 ms at loadavg
192. That isolates the cause rather than just shrinking the number, which is the
stronger answer. The quiet-machine absolute figure is **deferred** — the host
never fell below load 180 in three hours with three other sessions mid-run.

The zero-FK result is a product observation, not only an eval obstacle
(`docs/build-log/2026-07-30-c1-replication.md` §6):

- `episodic_memories.run_message_id` is **dead schema** — a bare nullable UUID
  with no ForeignKey (its neighbours `project_id`/`mission_id` have real ones),
  and `EpisodicMemoryService`'s sole construction site never passes it. No
  writer exists to fix.
- `memory_references` is **the real provenance table and it is empty in prod: 0
  rows**. It is properly built (NOT NULL FK to `run_messages`, `memory_type`
  CHECK includes `'episodic'`, unique on run_message+memory) and its docstring
  describes exactly the wanted edge. This is a genuine write-path gap.
- `run_messages.agent_run_id` at 0/191 is **by design** — a partial index for
  child-agent messages; NULL on a top-level turn is correct.

Caveat for any later convo-lane linking: `memory_references` records what the
**incumbent retriever surfaced**, so using it as retrieval ground truth is
circular. It is a candidate generator for adjudicated labels, never an oracle.

---

## 2026-07-31 — Preference write path: the substrate is fixed, and the score did not move ($0)

**Headline.** The typed write-router landed and **latest-state-wins is
0.3123236124176858 — bit-identical to 2026-08-01** — against BM25's
**0.3198494825964252**. ΔLSW −0.0075, cluster-bootstrap 95% CI
**[−0.0370, +0.0228]** over the **257 instances** (10,000 resamples, seed
20260801); misapplication **0.6717** vs 0.6736; neither-returned 0.0103 vs
0.0019. Cluster-permutation p = 0.657, exact McNemar p = 0.674 on 276
discordant probes, computed MDE 0.0445. **We still lose to a lexical baseline
at telling a live rule from a dead one, and it is the same loss.**

**Built (04 §13, `ffa640b8`).** `MemoryKind::Preference` minted (§13.2a) via
`20260731_006_preference_memory_kind`, classified **breaking** by the
classifier — widening a TEXT+CHECK enum is additive only under `25` §11c's
fallback read rule and the frozen kind enum is that rule's closed-Rust-reader
exception, so `schema_compat_revision` moved. `knowledge` deliberately NOT
minted (§13.2c: it is the arm name for `semantic`). `write_router_arm` is an
exhaustive match with no `_` arm (RW-1); `supersedes_own_kind` lifts the
hardcoded `existing.kind == MemoryKind::Semantic` into the dispatch, which
makes **RW-3 structural** — an arm can only name its own kind, so `episodic_arm`
provably supersedes nothing. RW-7: an untrusted preference hint degrades to a
belief.

**A real cross-kind bug fell out of the lift, attributed by bisect.** Before
it, an **Episodic** candidate with an explicit subject could close an open
**Semantic** generation. The regression test was run in a detached worktree at
`ffa640b8^` (`c666e459`) and **FAILED** there (`left: Supersede, right:
Append`); green at `ffa640b8`. Not base-relative.

**The migration's real cost was invisible, not syntactic.** The enum is 2 lines
and no exhaustive `match` on `MemoryKind` existed in `crates/*/src`. But **two
hardcoded five-kind lists** resolved the scope policy (`lib.rs`, `store.rs`); a
kind absent there gets no source, `context.allows` denies it, and the unit is
**invisible to both the compiler snapshot and recall** — the write lands and
nothing sees it. That is how minting first failed, silently, in a green test.
Both now iterate `MemoryKind::ALL`. **Lesson: RW-1's exhaustive match protects
the dispatch, not the enumerations elsewhere.**

**The flag arm (Arm F): the producer exists, the plumb is right, it mines
almost nothing here.** Verified all three claims — `extract_fact_candidates`
supplies subject/predicate → the stable `{scope}:{family}:{phrase}` key, no LLM;
gated on `fact_extraction_enabled` default `false`; **sole caller in the whole
tree was `bench_lme.rs:885`**, zero server wiring. Wired as
`MEMPHANT_FACT_EXTRACTION`, **default ON** (`40ba26cf`). Full arm, measured on
its own scratch DB: **2 semantic units from 8147 episodes**, zero edges, nothing
superseded, and the score **bit-identical** to the flag-off arm —
`ΔLSW = 0.0`, CI `[0.0, 0.0]`, **0 discordant probes**. Why: `extract_facts`
skips every line whose role is not `user` and MemoryCode is `Name: …` dialogue,
and every pattern is first-person self-report ("my favorite X is", "I switched
to") while MemoryCode's instructions are second-person directives.

**Why the key cannot be produced at $0 — measured, not argued.** A
gold-independent key derived from the **session body** recovers **8 / 1063**
gold groups (0.008); the best variant tried (single content word before the
quoted literal) reaches **221 / 1063** (0.208). Sessions restate a convention in
paraphrase. Deriving a preference key is an extraction problem, and extraction
is a `reflect` stage-1 LLM job that this lane's budget forbids.

**The size of the prize, priced by an ORACLE arm (Arm P, `decisional: false`,
NOT comparable to BM25).** Handing the instrument's own grouping key to the
write path: LSW **0.5795**, misapplication **0.3405** — Δ **+0.2672**
[+0.2375, +0.2985] and **−0.3311** [−0.3647, −0.2984] against the unchanged
arm. **The state machine fires at scale through Postgres**: 7198 `supersedes`
+ 3599 `contradicts` edges where the unchanged arm had **zero edges of any
kind**, 3599 superseded units with **0** open transactions, and
`remainders_recalled` = **0** so no valid-time-closed row ever reached a recall
result. **So state-machine is not what fails.** The residual 0.34
misapplication and the +0.063 that moved into neither-returned are the
session-level scoring identity (one session declares several conventions), not
supersession.

**Two ranking defects verified; one is NOT binding here.** `temporal_score`
(`lib.rs:10916`) is binary and its fusion tie-break (`lib.rs:7264`) is
**alphabetical on the body** — real, but **0 of 1063 MemoryCode probe queries
contain `current`/`latest`/`now`** (counted from the parquet) and every unchanged
unit is `Episodic`, not `Semantic`, so it scores 0.0 everywhere on this lane and
cannot be the binding constraint. `exact_score` (`lib.rs:10722`) tokenizes the
whole `fact_key`, which `derive_fact_key` prefixes with `{scope_id}:` — a
hyphenated UUID adds five constant tokens to every denominator, capping the
Exact channel. Real, not fixed here, flagged so a weak Exact signal is not
misattributed.

**The ceiling, recorded.** arXiv:2606.15903 (relayed by the coordinating
session, **not independently verified in this worktree**) puts deterministic
primitives at **63.4–68.3%** and a **mutation-time** LLM hook at 91.7–93.2%
(+22.6–24.1pt, ~$0.17/385 mutations, recall hot path untouched), because
identifier variants and intent-aware deletion are **not recoverable by any
keying scheme**. Our oracle-keyed 0.5795 sits just under that band. This work
reaches the deterministic baseline; it is not the frontier. The hook was **not
built**; `supersedes_own_kind` returns the *kind* an arm may close and never the
unit set, so target selection stays a separate step the hook can attach to.
arXiv:2607.21962 (same caveat) corroborates the write-path thesis: weak writes
fail downstream QA 24.2% vs 1.6%, OR 19.6.

**Verification.** `cargo test --workspace` green before and after the merge of
`accuracy-first` (`853a710d`). Migration class/boundary checks clean,
`check_spec_drift.py` clean after rsync to Syndai, `check_evidence_contract.py`
passes on all four analysis artifacts with a **computed** MDE. Scratch DBs only;
queue emptiness asserted from the database on the bench superuser credential
(8147/8147/8147, 0 pending, 0 failed) — never a worker self-report.

**`00` §4 five-doc migration done** (`5e85b641`): `04` (§13.0/§13.1/§13.2a/§13.6
re-scored honestly — the router is **PARTIAL**, not BUILT; hot-plane chain-head
injection still absent), `05` (per-kind retrieval row), `06` (actor gating),
`08` (`kinds` default + capability list), `20` (`memory.superseded`).

Report: `docs/build-log/2026-07-31-preference-writepath.md`. Artifacts:
`docs/build-log/artifacts/2026-07-31-preference-writepath/`. Commits
`ffa640b8`, `40ba26cf`, `880f7a81`, `853a710d`, `5e85b641`, `2babd08a` on
`af-w11-writepath`, **none pushed**. `paid_model_calls: 0`. **No checkbox,
default, cutover or SOTA claim moves** except `MEMPHANT_FACT_EXTRACTION`, which
is measured at exactly zero effect on this instrument.

---

## 2026-08-01 — S5 SWE-ContextBench stage 0 (`s5-swecb`, branch point `0e874da0`, not merged)

**Stage 0 gate: GREEN. `paid_model_calls: 0`, settled cost $0.00.** Preregistered primary statistic —
packed recall@5 of the official Relationship parent over 357 distinct targets, ANY-PARENT, with the
whole 1,007-row experience pool bound into **one** subject/scope as a single haystack — is **0.7591**
against a GREEN band of ≥ 0.50 committed at `fd2ebd7c` before the first ingest. Retrieval recall@5
(trace `fused_rank`) 0.7787; packed @1/@10/@25 = 0.5602 / 0.8291 / 0.8824.

**The miss profile names the lever.** Of 86 misses at k=5: **74 ranked below the cut**, 8 retrieved
but not packed, 4 never a candidate. Candidate coverage 98.9%. This is a **ranking** problem, not
candidate generation and not the packing budget, so `pack_render_cap` is worth at most ~2pp here.
This is also a free, 357-question, public-instrument bench for evaluating a reranker.

**Two premises in the plan of record were false and were corrected before any cell existed.**
(1) The tranche is **357, not 376** — `Related` is Lite(99) ⊎ Verified(166) ⊎ Multilingual(111)
concatenated, Lite ∩ Verified is exactly the 19 duplicated ids, and those duplicates are *not*
byte-identical. Confirmed independently by 357 Docker instance tags and 357 official case files.
`Experience` is 1,100 rows over **1,007** distinct ids. (2) The "$0 gold-patch pool" is **an answer
leak**, not a safe pool: 75.5% of gold parents touch a target patch file, 32.4% have an identical
touched-file set, **37.2% contain an exact target added line** and 29.8% share ≥50% of them, against
a same-repo random control of 9.1% / 0.13%. The temporal "predates by construction" premise is also
false — only 131 of 376 edges strictly predate, and Related's `created_at` inverts against PR number
on 23.6% of within-repo pairs (Experience: 0 of 60,372). Pool changed to patch-free prose, which
leaks at 9.0% against a **6.9% floor** set by targets quoting their own patches. Amendment
`0e137b54`, before any cell existed.

**Stage 2 ($504) recommended CANCEL as scoped, on evidence independent of s4-controls.** Table 3's
own numbers give a **+3.72pp ceiling** (no-memory 19.68 → oracle-summary 23.40, same scaffold). Our
expected effect is `0.759 × 3.72 =` **2.82pp**, an upper bound. MDE at the maximum available n of
**357** is 3.38pp (ψ=0.05) to 8.32pp (ψ=0.30) — **the expected effect is below the MDE at every ψ**,
with power 0.14–0.24 at realistic ψ. The instrument is too small for the effect it measures, which is
equally true of Table 4's own ranking and of Supermemory's 4.04pp at n=99. Also uncosted in the plan:
**~453 GB** of Docker pulls against 220 GB free, and **no Claude Code scaffold exists in this repo**.
Recommended instead: re-scope the primary endpoint to FAIL_TO_PASS (Table 4 moves it 19.64 → 55.95,
~10× the effect), and publish the retrieval result and the instrument audit, both already $0.

**Stage 0 turns out to have published comparators** — Table 5's Matched (%) is this exact endpoint
(Mem0 39.39 @k=3, OpenViking 51.52 @k=3, Supermemory 59.60 @k=15, LangMem 73.34 @k=10, all on the
300-row Lite pool). The retranche log's "both arms must be ours" holds for `Resolved` and **not** for
retrieval. A like-for-like Lite-scoped arm is running; the full-pool number is not directly
comparable and is not cited as if it were.

**Live defects, all caught at $0:** HTTP 422 on 300 of 1,007 rows shipping `created_at` with no
timezone (exactly the 41 multilingual repos vs the 12 Python ones, zero overlap); `rc` read from the
wrong end of a pipe reporting `EXITCODE=0` for a run that died; a cross-worktree `pkill` that killed
three sibling-lane servers; and shared `with_scratch_db.sh` holding a host-wide lock for whole runs —
trunk's fix `6fdcaf9d` adopted verbatim rather than re-derived, and the with-patch arm was
deliberately killed (rc=143, artifact refused, no DB orphaned) to unblock four lanes at loadavg 56.

**Verification.** `tests/test_swecb_stage0_recall.py`, 18 tests, green. Scratch DBs only; queue
emptiness asserted from the database on the bench superuser credential (1,007 completed, 0 pending,
0 dead), never a worker self-report; 0 degraded recalls; 376/376 traces fetched. Branch is purely
additive to its branch point apart from taking trunk's `with_scratch_db.sh`. Report:
`.superpowers/sdd/s5-swecb-report.md`. Build log: the S5 section of
`docs/build-log/2026-08-01-swe-contextbench-retranche.md`. Artifacts:
`docs/build-log/artifacts/s5-swecb/`. **No STATUS, ledger, default, cutover or SOTA claim moves.**
