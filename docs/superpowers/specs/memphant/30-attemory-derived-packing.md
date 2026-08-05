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

### P-1: Pointer-first render mode (code lane)

New pack lever `render_mode: Body | Pointer` on `PackLevers` (construction-time only, like the existing three; env `MEMPHANT_PACK_RENDER_MODE`; default `Body` = byte-identical today).

`Pointer` renders each packed item as one line of provenance coordinates, no body:

```
N. <source_ref>:<covering_source_span>  [unit_id]
```

using the existing `CorrectionHandle.source_span` / `source_ref` fields — no new provenance plumbing. Adjacent/overlapping spans from one source merge (gap ≤ 1 line/contiguous bytes) before rendering, single-point spans print bare. Rank ordinals only; never render scores.

Per-item cost collapses from O(body) to ~10–20 tokens, which converts the 64 Budget-drop misses into admissible items and makes `k` — not per-item cost — the sole binding constraint, matching what STATUS already says about the code lane.

Scope: **code lane only.** Chat-lane readers (LME) cannot dereference pointers; `Pointer` mode on the chat lane is a category error, not a tuning question.

### P-2: Merge adjacent selected chunks into one block

In `emit_selected_chunks`: when `select_chunk_mask` picks contiguous chunks, emit them as a single block with a single provenance header covering the merged span. This cuts the header tax roughly in proportion to adjacency *without raising any item's render budget* — i.e. it dodges the exact chat-lane cost regression that killed the reverted one-line fix. Independent of P-1; benefits both lanes.

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

- P-1/P-3: Track R, paired vs current `Body/k=10` arm, same lattice, same locked bank. Success bar: in-pool-unpacked misses (currently 64) → near zero, hits@10 delta positive, reader-QA non-inferior. This is the direct rematch on the ground grep won.
- P-2: LME-S n=5 smoke first (chat-lane cost guard — the reverted fix's failure mode), then rung-7 profile.
- P-4: rides the C0 drift-test pattern; behavioral effect only measurable end-to-end in Syndai — defer quantitative claim, land as contract change.

Per [[27-sota-ladder-and-validation]] discipline: all levers default OFF, each promoted only on its own paired evidence.

## 4. Explicitly not taken from Attemory

- Attention-over-KV retrieval, resident model, segment KV persistence — "minus local" by decision, and our C2 verdict already showed the retrieval deficit is structural, not a missing-scorer problem.
- Per-segment top-k + 8-pass cross-segment reconciliation — artifact of incomparable per-segment scores; MemPhant's fused scores are globally comparable, so this layer collapses to nothing.
- `1/log2(rank+1)` chunk→file fusion — P1 already landed max-pool chunk rerank granularity; a second aggregation scheme is YAGNI until max-pool measurably fails.
- `remaining_budget` returned from writes; content-addressed index-per-commit — no current consumer.
