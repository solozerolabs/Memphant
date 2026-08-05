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

- P-2 (landed, unmeasured): LME-S n=5 smoke first (chat-lane cost guard — the reverted fix's failure mode), then rung-7 profile, then Track R paired vs lever-off. **No promotion claim until that evidence exists** — the lever is landed and default OFF, which is not the same as measured. Note the mechanism only bites where a unit's selected chunks are *contiguous*; the code lane's in-pool-unpacked misses were per-item Budget drops, so the expected effect there is more items admitted per budget, and that is the number to read.
- P-3: Track R, paired vs current `k=10` arm, same lattice, same locked bank. With P-1 rejected, render cost no longer collapses, so raising render-k is not free — measure `candidate_k` vs `k` separately.
- P-4: rides the C0 drift-test pattern; behavioral effect only measurable end-to-end in Syndai — defer quantitative claim, land as contract change.

Per [[27-sota-ladder-and-validation]] discipline: all levers default OFF, each promoted only on its own paired evidence.

## 4. Explicitly not taken from Attemory

- Attention-over-KV retrieval, resident model, segment KV persistence — "minus local" by decision, and our C2 verdict already showed the retrieval deficit is structural, not a missing-scorer problem.
- Per-segment top-k + 8-pass cross-segment reconciliation — artifact of incomparable per-segment scores; MemPhant's fused scores are globally comparable, so this layer collapses to nothing.
- `1/log2(rank+1)` chunk→file fusion — P1 already landed max-pool chunk rerank granularity; a second aggregation scheme is YAGNI until max-pool measurably fails.
- `remaining_budget` returned from writes; content-addressed index-per-commit — no current consumer.
