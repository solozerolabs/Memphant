# MemPhant - Attemory-Derived Packing Levers

> Status: SPEC (2026-08-05). No code landed. Source study: AttemorySystem/attemory (MIT, attention-over-KV retrieval, local Qwen). We take zero attention machinery — every lever here is model-free packing/rendering discipline. Local-model retrieval explicitly out of scope (D: "minus local").

## 0. Rule

Adopt only mechanisms that attack the measured rung-7 bottleneck: per-item render cost, not retrieval order. Anything requiring a resident model, globally-incomparable per-segment scores, or KV persistence is an artifact of Attemory's fixed context window and does not transfer.

## 1. Evidence base

| claim | value | source_status |
|---|---|---|
| Track R in-pool-unpacked misses are 100% Budget drops (per-item cost, not total budget) | 64/64 | reproducible ([[27-sota-ladder-and-validation]], rung-7 verdict) |
| Chunk provenance-header tax: uncapped chunked item can never fully emit; one-line fix measured and reverted (chat-lane regression) | build-log 2026-07-30-coding-lane-first-win.md:122, STATUS.md:126 | reproducible |
| `pack_render_cap` is a null on the code lane; binding constraint is `k=10`, not the 8192 budget | STATUS.md:124 | reproducible |
| Attemory SWE-QA: pointer-only hint (~<200 tok) cut Claude Code total tokens 285.39M→160.39M (-43.8%), judge delta -0.23/100, 720 paired samples | benchmarks/sweqa.md | vendor_reported |
| Attemory subagent tokens -54.7% vs main -29.4% — the hint's anti-broadening + subagent-routing prose moved exploration, not just shrank it | sweqa.patch summarize_run.py | vendor_reported |
| Agentic grep 96.67% vs MemPhant 58.89% hits@10 on Track R | p=1.2e-19 | reproducible |

The vendor numbers are directional only, but the *shape* — coordinates beat payload when the reader has `Read` — is the same conclusion our own grep result forced.

## 2. Levers (in priority order)

### P-1: Pointer-first render mode — REJECTED 2026-08-05, premise does not hold

**Status: not implemented. The blocker is structural, not effort.**

Attemory's pointer win rests on a precondition MemPhant does not satisfy: its pointers are `path:line-range` into a repo the reading agent already has `Read` access to, so a coordinate is losslessly exchangeable for content. In MemPhant:

- `source_ref` is an **opaque provenance token everywhere**, never a locator. Episodes carry whatever the caller passed — the code lane mints `coding-event:{attempt_id}:{sequence}:{event_id}` (`scripts/code_lane_run_memphant.py:650`); the file plane mints `file-sync:{plan_sha256}:{index}` (`crates/memphant-core/src/service.rs:4657`). Neither is a path.
- `CorrectionHandle.source_span` is a **byte range into the unit's own stored body**, not into any file on disk.
- There is **no dereference endpoint**. The served surface is health/openapi/episodes/recall/reflect/correct/forget/mark/file-sync/traces/scopes/context-bindings (`crates/memphant-server/src/lib.rs:33-45`) — nothing resolves a unit id or a span back to text.

So a pointer render would hand the reader a string it has no way to open, scoring zero on any QA metric. The deeper reason is the same asymmetry [[memphant-grep-beats-us]] established, read the other way: grep wins on repo-recoverable facts *because* those are dereferenceable, and MemPhant's niche is precisely the content that is **not** in the repo — which is exactly the content a pointer cannot address. Pointer-first rendering is coherent only for memories that mirror a corpus the reader independently holds, and MemPhant's corpus is ingested trajectory content whose only copy lives in MemPhant.

Reopen only if a dereference tool ships (a recall-by-unit-id/span read path the client can call), and even then the render must be measured against body rendering, not assumed better.

### P-1 (original, superseded)

New pack lever `render_mode: Body | Pointer` on `PackLevers` (construction-time only, like the existing three; env `MEMPHANT_PACK_RENDER_MODE`; default `Body` = byte-identical today).

`Pointer` renders each packed item as one line of provenance coordinates, no body:

```
N. <source_ref>:<covering_source_span>  [unit_id]
```

using the existing `CorrectionHandle.source_span` / `source_ref` fields — no new provenance plumbing. Adjacent/overlapping spans from one source merge (gap ≤ 1 line/contiguous bytes) before rendering, single-point spans print bare. Rank ordinals only; never render scores.

Per-item cost collapses from O(body) to ~10–20 tokens, which converts the 64 Budget-drop misses into admissible items and makes `k` — not per-item cost — the sole binding constraint, matching what STATUS already says about the code lane.

Scope: **code lane only.** Chat-lane readers (LME) cannot dereference pointers; `Pointer` mode on the chat lane is a category error, not a tuning question.

### P-2: Merge adjacent selected chunks into one block — LANDED 2026-08-05, default OFF

Implemented as `PackLevers::merge_chunk_blocks` (`crates/memphant-core/src/lib.rs`), threaded exactly like `pack_render_cap`: construction-time only, no `RecallRequest`/wire/OpenAPI field. Builder `MemoryService::with_merge_chunk_blocks`, env `MEMPHANT_PACK_MERGE_CHUNK_BLOCKS`, bench flag `--merge-chunk-blocks`, trace flag `pack_merge_chunk_blocks`.

**Mechanism.** `selected_runs` groups selected chunks into inclusive runs that are both *adjacent* and *header-mergeable*; each run renders as one block under a single run-spanning header. Mergeability is decided per header dialect, so the merge is lossless in both:

- Episode chunker: `[episode {id}] [kind {k}]{date} [{label} {first}-{last}]` — every slot is fixed across a unit's chunks except the trailing span, so a run's header is the first chunk's with the span widened to the run's last (`split_header_span` / `run_header`).
- Resource chunker: one section heading repeated verbatim across the section's chunks, so a run merges with no span surgery.
- Anything else (foreign episode, unparseable header) does **not** merge, and a positional gap always starts a new block — the reader still sees a distinct header wherever content is discontinuous.

**Why it is safe against the reverted fix's failure mode.** Selection is untouched: `select_chunk_mask`/`expand_siblings` still charge the *un-merged* per-chunk price, so the admitted chunk set is byte-identical and the gate can only ever reserve more than the merge spends. A single-chunk run renders and prices byte-identically to the pre-P-2 path. The merge is therefore a strict cost reduction, never an increase — the opposite sign from the 2026-07-30 one-line fix that raised per-item cost and was reverted.

**What it unlocks.** `chunk_completion_pass` now prices full coverage through the same merged accounting, which is what makes full coverage reachable at all: pre-merge it cost one header *per chunk* on top of the bodies, so it always exceeded the whole-body render budget and a chunked item was structurally unable to emit all of itself. Under the lever, a budget that previously bought only the bare whole body (all provenance lost) buys every span *with* its header. That paired case is asserted directly in `partial_chunk_render_completes_itself_only_when_leftover_budget_allows`.

Tests: 5 added/updated in `chunk_render_tests` + `pack_cost_tests`; `levers_off_pack_is_byte_identical` is untouched and passing, which is the guarantee that the default path did not move. Full workspace green (152 core lib tests), fmt + clippy clean.

### P-3: Split candidate-k from render-k (code lane)

Attemory runs candidate pool 16–20 → render 8. MemPhant's code lane conflates them at `k=10`. Add `candidate_k` (pool seen by rerank/dedup/contradiction logic) distinct from `k` (items rendered). With P-1 making renders near-free, render-k can rise instead — measure which; do not add both knobs if one suffices.

### P-4: Render-block instruction discipline (client render layer, C0 contract)

The packed-context wrapper emitted to strict-contract clients adopts Attemory's prompt shape:

- "Inspect these first; a shortcut, not a complete answer scope."
- "Broaden only to fill a concrete, named evidence gap — never because results were provided."
- Multi-hop verification routed to a subagent returning a concise paths+lines summary.
- Empty result = the literal line `No relevant memories found.`; wrapper omitted entirely when empty. No scaffolding tax.

This is prose in the client render template (Syndai adapter + spec-28 contract), not engine code. Version the template string (Attemory's `context_structure` fence) so render-format drift is detectable by the existing drift tests.

## 3. Measurement

**LME-S n=5 smoke — RUN 2026-08-05, paired, PASS (smoke only, not evidence).** Both arms `--sample 5 --seed 1 --k 10`, `--features fastembed`, ephemeral scratch DB per arm via `with_scratch_db.sh`, identical dataset sha. Result:

| | lever OFF | lever ON |
|---|---|---|
| recall@5 / recall@10 | 0.8 / 0.8 | 0.8 / 0.8 |
| `hit_at_5`, `hit_at_10`, `first_answer_rank`, `degraded` | — | **identical on every question** |
| `returned_items` | 4, 5, 6, 5, 6 | 5, 6, 7, 5, 7 |

Every scalar in both reports matches except the flag itself. The lever fires and does exactly what the mechanism predicts: **+1 packed item on 4 of 5 questions at the same 8192-token budget**, with no change to which items rank where. That is the correct sign — cheaper per-item cost admits more evidence, the opposite of the reverted fix's failure mode.

Three limits on reading this:
1. **n=5 cannot promote anything.** It clears the CI chain-smoke bar and shows no regression; it is not a measurement.
2. **Total reader tokens did not fall.** Packing fills the budget either way; what changed is how many distinct items fit inside it. "Cheaper per item" is not "fewer tokens sent."
3. **No reader ran** — this lane is `retrieval_only`, so the reader-QA question (does more, smaller evidence answer better?) is untouched. Recall at n=5 was insensitive: the single miss stayed a miss.

**Rung-7 profile — RUN 2026-08-05. Passes, but carries NO signal on P-2: the lever is inert across the whole golden lane.**

| what ran | result |
|---|---|
| `profile rung7-packing-abstention-profile.yaml --compare-to rungs-0-6-baseline` | pass (0 activated, 15 dormant) |
| `run rung7-state-style-sampled.yaml` OFF / ON | 2/2 / 2/2 |
| `run rung7-baseline-sampled.yaml` OFF / ON | 2/2 / 2/2 |
| `run golden.yaml` OFF / ON | 10/10 / 10/10 |
| `verify-golden golden.yaml` OFF / ON | 10/10 / 10/10 |

Archived traces for both arms are byte-identical apart from `latency_micros` and `trace_id` — run-to-run noise. **Zero chunk-rendered items occur anywhere in the lane.** The cause is structural, not incidental: golden fixtures seed short bodies, and `episode_contextual_chunks` emits nothing below its window threshold, so units arrive with 0 or 1 chunk. Even `contextual_chunk_breaker`, the one golden built to exercise chunk-aware recall, carries exactly **one** chunk — and a single chunk can never form a contiguous run. `selected_runs` returns one single-index run, which renders and prices byte-identically to the pre-P-2 path by construction.

So the rung-7 result is a clean **non-regression** on the deterministic lane and nothing more. It cannot promote P-2, and quoting "rung-7 passes" as support for the lever would be a category error — the gate never touched the mechanism.

To make the golden lane capable of gating this at all, it needs a fixture whose unit carries ≥3 contextual chunks with an adjacent selection, asserting (a) one run-spanning header replaces the per-chunk ones and (b) full coverage becomes reachable. That fixture does not exist yet; the merge's behavioural coverage currently lives only in `memphant-core` unit tests and the LME-S lane.

**Harness seam added.** `crates/memphant-eval/src/lib.rs` previously hardcoded `PackLevers::default()`, so no suite could run a lever arm. It now reads `MEMPHANT_PACK_MERGE_CHUNK_BLOCKS` — the same var the runtime reads — via `eval_pack_levers()`. Unset means off, so every existing suite is unchanged; an unrecognised value is a hard error rather than a silent default, because an eval that quietly measures the wrong arm yields evidence that looks paired and is not.

**Track R proper — CANNOT RUN, data absent.** `benchmarks/data/track_r_repo_memory_golden.jsonl` (180 cases, 373,968 bytes, sha256-pinned) does not exist on disk; only `track_r_repo_memory_golden.lock.json` survives, same for the paraphrase bank. This is the C3 wipe. Regeneration is not a rerun: it needs the 64,055-event `nebius/SWE-rebench-openhands-trajectories` materialization plus `track_r_mine.py`, whose generation and adjudication run as an **agent-in-the-loop request/reply cache** (`--stage mine` exits 2 while calls are pending). A cold cache is a multi-round loop, not a command. Note also the lock records `bar_passed: false` (`with_distractors_ge_50pct` failed), so the instrument had not cleared its own preregistered bar.

**Code lane paired (R0 coding-events bank, n=40) — RUN 2026-08-05. Retrieval null, cost win, Budget drops cut 61%.**

Substituted for Track R because its bank is present and its runner (`code_lane_run_memphant.py`) *is* the Phase-1b Budget-drop replay harness. Both arms: same corpus/golden (lock verified, sha `b7bf9b34959c`), fresh scratch DB and tenant each, `k=10`, `mode=deep`, `budget_tokens=8192`, retrieval-trace only — no reader, no model call, $0.

| | merge OFF | merge ON |
|---|---|---|
| R@5 / R@10 | 0.625 / 0.750 | 0.625 / 0.750 |
| hit@5 / hit@10 counts | 25 / 30 | 25 / 30 |
| per-question `hit_at_5`, `hit_at_10`, `gold_rank`, `bucket` | — | **zero differences** |
| **Budget drops** | **38** | **15** (−61%) |
| `output_limit` drops | 1538 | 1561 (+23) |
| `trust` drops | 11 | 11 |
| **packed context chars** | **768,200** | **678,890** (−11.6%) |
| packed items | 400 | 400 |
| `packed_item_chars_max` | 6537 | 6605 |

Reading it: the merge does exactly what the mechanism says. Budget evictions fall by 61% and reader-facing context by 11.6%, at byte-identical retrieval. The freed budget converts into `output_limit` drops (+23) rather than into recall, because **this lane is slot-bound at `k=10`, not budget-bound** — `packed_items_mean` is exactly 10.0 and `budget_share_of_in_pool_unpacked` is 0.0 in *both* arms. Cheaper items cannot buy slots. That is the same null mechanism that made `pack_render_cap` a code-lane null (STATUS.md:124), and it was predictable from the baseline arm alone. The lone rise, `packed_item_chars_max` 6537→6605, is the completion pass buying fuller coverage for one item now that it is affordable — the intended behaviour, not a regression.

So on the code lane P-2 is a **context-cost win at zero retrieval cost**, not a retrieval win. Unlike LME-S — where the lane is budget-bound and the saving refilled the budget with a 5th/6th/7th item — here the budget was never the binding constraint, so the saving stays visible as 11.6% less context sent. Same mechanism, opposite surface, because a different constraint binds.

Caveats: n=40, single seed, one bank. The 11.6% is a deterministic mechanical measurement and trustworthy at this n; the retrieval null is not powered to exclude small effects. **This is not Track R** and must never be reported as such — Track R is the 180-case repo-memory instrument that produced the 58.89%-vs-grep-96.67% result, and it remains unrunnable.

**Chunk-bearing fixture — ADDED 2026-08-05.** `examples/evals/golden/contextual_chunk_multi_window.yaml`: four contiguous windows in the production episode header dialect. Passes 11/11 in both arms, and by design cannot distinguish them — a golden grades units, not rendered text. `multi_window_fixture_guard` in memphant-core pins what the golden cannot: all four windows selected as one run at the declared budget, merge shrinks the render 542→398 chars (−26.6%), one `[turns 1-8]` header replaces four, answer-bearing window survives. The guard exists because a fixture that drifts into whole-body fallback stays green while covering nothing — exactly how the lane lost this coverage originally.

## 5. Verdict on P-2 — SUPERSEDED 2026-08-05 by the n=178 dev-cohort run (see §6)

The verdict below said "zero measured quality effect on any lane." That was **wrong**, and the error was one of statistical power, not mechanism. It was drawn from n=5 (too small to resolve a 3-point effect) and the n=40 code lane (slot-bound, so structurally null). At the full **n=178 dev cohort the merge produces a real, deterministic retrieval-survival win** — kept here struck through so the reasoning error is visible, not erased.

> ~~**Not negative. Not yet useful. It is a cost lever with zero measured quality effect on any lane.**~~
>
> | lane | binding constraint | retrieval | what the merge bought |
> |---|---|---|---|
> | LME-S n=5 | budget | 0.8/0.8, unchanged | +1 packed item on 4/5 questions |
> | Code lane n=40 | slots (`k=10`) | 0.625/0.750, byte-identical | −11.6% packed context, Budget drops 38→15 |
> | Golden lane | — | 11/11 both arms | nothing (unit-level assertions) |
> | Unit (fixture shape) | — | — | −26.6% render |
>
> ~~It has never improved an answer.~~ ~~retrieval was identical on every lane and every question measured~~ — **false at n=178; the n=5 arm simply could not see it.**

**Lesson for the ladder:** a null at n=5 is not a null. The spec's own §3 called for the LME-S run at the dev-cohort size; the interim n=5 smoke was mistaken for the measurement. Do not read an underpowered smoke as evidence of no effect — [[memphant-memorycode-gold-is-recency]] is the same trap from the other side.

## 6. The n=178 dev-cohort result (2026-08-05)

**Retrieval survival: +3.61 points, deterministic, CI excludes zero.**

Paired arms on the frozen 178-question development cohort (`longmemeval_s.development.json`, seed 20260713, k=10, budget 8192, session granularity, fast mode, bge-small), fresh scratch DB per arm, dataset/seed/n pairing verified in-process.

| | merge OFF | merge ON |
|---|---|---|
| recall@5 = recall@10 | 0.6506 | **0.6867** |
| paired Δrecall (n=166 scored) | — | **+0.0361, CI95 [+0.0120, +0.0663], excludes 0** |
| abstention correct | 7/12 | 7/12 (unchanged) |
| gold miss→hit / hit→miss | — | **6 / 0** (monotone) |

`recall_at_k` here is **post-pack survival** (`bench_lme.rs:627`, the rank of the first gold-bearing *packed* item), not pre-pack ranking. In all 6 flipped questions the base arm had the gold-bearing item **unpacked** (`first_answer_rank = None`); under the merge it surfaces at rank 1–3 — and on 3 of the 6 the arm actually packed *fewer* items total. So this is not "freed budget adds items"; the cheaper merged render changes the greedy fill's admission/eviction so the gold-bearing item wins a slot it previously lost. This is the budget-bound mechanism §5's own text predicted, now measured.

**Determinism proven cold:** two independent OFF runs on separate scratch DBs are per-question identical (0/166 hit mismatches), and all 6 flips are stable misses in both. The +6 is a real effect of the lever, not scratch-DB ordering variance.

**Reader QA (paid, funded 2026-08-05):** luna-pro reader / sol-pro judge, prompt-v3, longmemeval judge profile, both arms n=178, on the `final_user_requested_screen.development_reader` lattice. This is the endpoint gate — does the gold that newly survives packing convert to correct *answers*, and does the header-merge on the other ~172 questions regress anything? Expected discordance is small (~6 material flips), so the test is powered to confirm direction + non-regression, not to resolve a sub-3-point QA effect. Result recorded in §7 once the run settles.

## 7. Reader-QA result (2026-08-05, paid, settled $5.94)

luna-pro reader (high) / sol-pro judge, longmemeval profile, prompt-v3, both arms n=178, one shared ledger + cache. Settled spend $5.94 total (mergeoff $2.16, mergeon $3.78 — 48 cross-arm cache hits and real prices far under the $10/$40 ceiling). Pipeline was $0-stub-validated first; the packet minter's `splitlines()` bug (fixed in `0007ec1b`) had to be repaired before minting.

**Headline: QA accuracy 0.3539 → 0.4101, +5.62pt, significant — but fragile.**

| | value |
|---|---|
| answer accuracy OFF → ON | 0.3539 → 0.4101 (+5.62pt) |
| paired McNemar cells (B improved / C regressed) | 15 / 5 |
| net | +10 questions |
| exact two-sided McNemar p | **0.0414** |
| bootstrap Δ CI95 (in-report) | [+0.0112, +0.1067], excludes 0 |
| discordant pairs | 20 of 178 |

**Two caveats that keep this from being a clean promote on QA alone:**

1. **Borderline p with an unmeasured reader-noise floor.** p = 0.041 is just under 0.05. I tried to show the discordance is lever-driven rather than reader nondeterminism (luna-pro at high effort is not guaranteed deterministic) by checking whether discordant pairs coincide with real evidence changes — but **99% of all 178 questions had packed-set changes** (the retrieval layer is deterministic, but the merge perturbs almost every pack), so "20/20 discordant changed" does not discriminate signal from noise. Only 2 questions had unchanged evidence (both concordant), far too few to bound the reader's self-flip rate. **A same-arm reader replicate on a fresh cache (~$6) is required to state whether p=0.041 survives the noise floor.** Not yet run.
2. **Abstention regressions.** Of the 5 regressions, **3 are `_abs` questions** (`60bf93ed_abs`, `edced276_abs`, `gpt4_70e84552_abs`) — the merge pushed the reader off a correct abstention onto a wrong answer. Abstention correctness at the retrieval layer was unchanged (7/12), so this is a reader-behaviour effect of the reshuffled pack, and it is the specific failure mode to watch.

**What is robust vs fragile:**
- **Robust:** retrieval-survival +3.61pt (§6), deterministic, 6-0 monotone, two OFF runs per-question identical. Same evidence class `pack_render_cap` was promoted on.
- **Fragile:** QA +5.62pt — directionally positive, nominally significant by two tests, and only 3 of the 15 improvements trace to the deterministic gold-survival flips; the rest ride the broader pack reshuffle whose net sign is +10:−5 but whose per-question stability is unproven.

### 7a. Noise-floor replicate (2026-08-05, paid, settled $2.16) — SIGNAL SURVIVES

A third arm re-ran the OFF evidence through the reader/judge on a **fresh cache** (real API calls, identical inputs, lever unchanged), to measure the reader's self-discordance directly.

| comparison | discordant | net | exact p |
|---|---|---|---|
| **noise floor** — OFF vs OFF-replicate (nothing changed) | **7 / 178 (3.9%)** | **−1** (3 up, 4 down — symmetric) | 1.00 |
| **lever** — OFF vs ON | 20 / 178 | **+10** (15 up, 5 down) | 0.0414 |
| **lever, noise-stable subset** — drop all 7 noise-unstable questions | 15 | **+9** (12 up, 3 down) | 0.0352 |

The reader's self-noise is real but small (3.9%) and **directionally balanced** (net −1). The lever's effect is 3× that discordance count and, unlike noise, **asymmetric and positive** (+10). Removing every question that showed any re-run instability still leaves **+9 net at p=0.035**. So the +5.6pt QA lift is not an artifact of a noise floor that happened to lean positive — the signal survives noise subtraction.

**Abstention failure mode, quantified.** Of the 5 lever regressions, 2 were noise-unstable; **3 are stable, and 2 of those 3 are abstention questions** (`60bf93ed_abs`, `gpt4_70e84552_abs`) — the merge reshuffles the pack such that the reader answers where it should have abstained. This is real but small (2 stable abstention regressions against 12 abstention questions and a net +9 overall) — a watch-item, not a blocker.

**VERDICT: PROMOTE.** Two independent, mutually reinforcing results, total reader-QA spend **$8.10**:
- **Retrieval survival +3.61pt** — deterministic, 6-0 monotone, two OFF runs per-question identical. The mechanism (`pack_render_cap`-class post-pack answer-bearing survival).
- **Reader-QA +5.62pt** — exact McNemar p=0.041, and it survives the measured 3.9% noise floor (stable-subset +9, p=0.035).

**Recommended action:** flip `merge_chunk_blocks` default ON. This is a production behaviour change — it makes the shipped packer emit merged chunk blocks, so `levers_off_pack_is_byte_identical` becomes `default_on_pack_merges_chunks` and the rung-7 profile / STATUS.md record the promotion. Land it as its own commit with the abstention watch-item noted, so the default flip is reviewable in isolation.

### 7b. Default flipped ON — LANDED 2026-08-05

`merge_chunk_blocks` is now the shipped default. The type default and every composition root agree (the discipline the `lexical_scorer` default enforces):

- `PackLevers::default().merge_chunk_blocks == true` — hand-written `Default` impl (not `#[derive]`) so the one non-false default is explicit and greppable.
- `merge_chunk_blocks_from_env()` (runtime) and `eval_pack_levers()` (golden lane) both resolve unset ⇒ `true`; agreement is asserted by `merge_chunk_blocks_defaults_on_and_agrees` in the runtime crate.
- Control arm: `MEMPHANT_PACK_MERGE_CHUNK_BLOCKS=0` (or `false`/`off`); bench flag `--no-merge-chunk-blocks`; garbage still fails closed.
- Trace flag inverted to `pack_merge_chunk_blocks_disabled` (a trace flags deviations from the default, and the default is now merged).
- Renamed `levers_off_pack_is_byte_identical` → `control_arm_all_levers_off_matches_pre_lever_golden`; pack-cost tests that measure the per-chunk baseline pin merge off via an `all_levers_off()` helper; the shipped-recall integration test (`recall_chunk_renders_matched_window_plus_neighbour`) now asserts the merged invariant (one run-spanning header, provenance never lost).

Full workspace green, clippy clean. `PackLevers::default()` is no longer a neutral zero — a true no-levers baseline is now `PackLevers { merge_chunk_blocks: false, ..PackLevers::default() }`.

**Abstention watch-item (carried forward):** 2 stable abstention regressions of 12 abstention questions. Not a blocker at net +9, but if a future abstention-specific gate is built, this is the first thing to re-measure.

~~**Recommendation.** ... Either way the header-merge's effect on abstention questions gets a dedicated check before promotion.~~ *(superseded by 7a — the replicate was run, the noise floor is 3.9%, the signal survives, and the abstention effect is quantified above.)*

- P-2 (landed, smoke-passed, unmeasured): rung-7 profile, then Track R paired vs lever-off. **No promotion claim until that evidence exists** — the lever is landed and default OFF, which is not the same as measured. Note the mechanism only bites where a unit's selected chunks are *contiguous*; the code lane's in-pool-unpacked misses were per-item Budget drops, so the expected effect there is more items admitted per budget, and that is the number to read.
- P-3: Track R, paired vs current `k=10` arm, same lattice, same locked bank. With P-1 rejected, render cost no longer collapses, so raising render-k is not free — measure `candidate_k` vs `k` separately.
- P-4: rides the C0 drift-test pattern; behavioral effect only measurable end-to-end in Syndai — defer quantitative claim, land as contract change.

Per [[27-sota-ladder-and-validation]] discipline: all levers default OFF, each promoted only on its own paired evidence.

## 4. Explicitly not taken from Attemory

- Attention-over-KV retrieval, resident model, segment KV persistence — "minus local" by decision, and our C2 verdict already showed the retrieval deficit is structural, not a missing-scorer problem.
- Per-segment top-k + 8-pass cross-segment reconciliation — artifact of incomparable per-segment scores; MemPhant's fused scores are globally comparable, so this layer collapses to nothing.
- `1/log2(rank+1)` chunk→file fusion — P1 already landed max-pool chunk rerank granularity; a second aggregation scheme is YAGNI until max-pool measurably fails.
- `remaining_budget` returned from writes; content-addressed index-per-commit — no current consumer.
