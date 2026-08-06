# OctoBench census + adherence-injection cheap look (2026-08-06)

**Spend:** ~$15–25 (sonnet trajectories + gpt-4o judge), all in a throwaway
scratchpad — nothing here ran against the repo or the `xsession-controls`
adherence branch. **Verdict:** session-start rule injection is **flat** on a
blind external instrument; do not scale to the full run. Folded here for the
adherence lane to pick up (see the plan on `xsession-controls`).

## Why this look ran

After the adherence 9-team synthesis (veto dead per G1; injection is the
surviving bet; the 75%/32% demand is single-operator), we needed a **blind,
external, multi-repo** test of the injection bet — something a single-operator
transcript gate can't give. OctoBench is that instrument.

## Census — OctoBench (usable, MIT)

- Paper: arXiv 2601.10343 (submitted 2026-01-15), "Scaffold-Aware Instruction
  Following in Repository-Grounded Agentic Coding."
- Data: HF `MiniMaxAI/OctoBench` — **MIT**, 217 tasks / 34 Docker images /
  ~6,778 checklist items. Categories: SP 55, Skill 46, Claude.md 35, memory 29,
  AGENTS.md 25, User Query 27. 179 tasks use the **Claude Code** scaffold.
- Harness: `MiniMax-AI/mini-vela` (README-MIT; no detected LICENSE file — verify
  before depending on harness *code*). LiteLLM proxy intercepts calls; each task
  runs in an `linux/amd64` Docker image (QEMU-emulated on arm64); grading is
  **gpt-4o LLM-as-judge** over binary checklist items.
- **Fit:** measures *does injection beat baseline* (enforcement value). It does
  NOT exercise MemPhant's cross-session **capture** wedge (rules are given
  upfront), so it cannot distinguish MemPhant from a trivial re-paste. Full run
  (217×3) ≈ $300–1K + ~250 GB of images — belongs on native amd64, not local.

## The look

- Integration: a `claudecode-inject` scaffold (~40 lines, `inject.py`) —
  subclasses the stock scaffold, installs a `SessionStart` hook emitting a
  ≤4 KB rule block (`INJECT_RULE_BLOCK`), fail-open to stock when empty. One
  mechanism, harness supplies the block; this is the death-from-below control.
- Design: 6 tasks × 2 arms off one image (`md_course_builder`), sonnet-4-5 SUT,
  gpt-4o judge. Baseline = rules where OctoBench puts them (workspace CLAUDE.md /
  system_prompt); inject = same rules surfaced at session start.
- Cost calibration (one baseline trajectory): 28 API calls, ~911K cumulative
  input tokens, **~$0.60 (cached) – $2.79 (uncached) per trajectory**.

## Result — FLAT

| arm | checklist pass |
|---|---|
| baseline | 107/112 = **95.5%** |
| inject | 108/112 = **96.4%** |
| delta | **+0.9pp** |

**Exactly one check of 112 flipped** (a Chinese-language rule); all others were
identical between arms. Machine result: `artifacts/octobench-injection-look/
result-summary.json`.

## Interpretation

- **No lift.** Injection did essentially nothing.
- **Root cause: saturation + wrong regime.** Frontier sonnet is already 95.5%
  adherent on these short tasks → no headroom. And OctoBench tasks are short
  (~28 calls); the adherence plan's own G3 found real violations sit at turn
  ~490+ of 3,000-turn sessions. So OctoBench tests the *easy* regime, where the
  knowledge-action gap is smallest. Same "death from below" as the retrieval
  axes.
- **Caveats:** thin look (5 usable tasks — one dropped on a convert failure, one
  had an empty checklist; one model, one repo). Not powered. But the direction
  is unambiguous and the saturation is structural.

## Calls

1. **Do not scale to the full OctoBench run** — it would measure saturation, not
   injection value.
2. OctoBench (short tasks, frontier model) **does not validate the injection
   bet**; frontier models already comply. It also never tested the capture wedge.
3. If injection has value it is at **depth, in long real sessions with real
   corrections** — the Phase A live cohort, not a short-task benchmark. Let the
   cohort arbitrate; if it is flat at depth, adherence collapses to a Syndai
   plumbing fix (inject rules at turn-1, wire the discarded memory kwargs).

## Reproduce (native amd64 recommended)

`artifacts/octobench-injection-look/` holds `inject.py` (the scaffold — drop into
mini-vela `scaffolds/` and register), `run_look.py` (arm orchestration),
`judge_look.sh` (convert + gpt-4o judge), and `result-summary.json`. Pull an
image (`minimaxai/feedfeed:<tag>`), run a LiteLLM proxy with `ANTHROPIC_API_KEY`,
judge with `OPENAI_API_KEY`. Related: `[[memphant-octobench-injection-look]]`,
`[[memphant-adherence-9team-synthesis]]`.
