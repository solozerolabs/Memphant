# aidevops memory-subsystem mechanism eval — handoff

Date: 2026-08-09
Repository: `/Users/sidsharma/Memphant`
Branch: `codex/outcome-coupled-evolution`
HEAD at handoff: `9ef7a68c`
State: **DELIVERABLE COMPLETE (docs-only), UNCOMMITTED** — no code, no paid work, no new default.

## What was asked

Evaluate three mechanisms from `marcusquinn/aidevops`'s memory subsystem for MemPhant
adoption. Deliverable = decision-register entry (doc 26) + spec deltas where adopted +
a doc-13 prior-art paragraph. NOT code. License caveat: MIT with `ATTRIBUTION.md`
attaching notice to copied code AND "distinctive operating patterns" → reimplement
concepts, copy nothing. Held to standing evidence rules (packaged-Postgres promotion
evidence, $0 qualification first, no tuning on burned tranches, ranking change ships
only with demonstrated lift — the Track-R reranker bar).

## What was delivered (3 files edited + private mirror synced)

1. **`docs/superpowers/specs/memphant/26-decision-register.md` §9** — the decision-register
   entry, three dated decisions:
   - **D-2026-08-09a — Q-value fold into ranking → DORMANT (activation-gated).** Already
     the specced rung-11 fold engine + `mark` verb; aidevops confirms the shape, is not
     evidence for a default. Can't qualify on static banks. Gate = preregistered
     dogfood-volume floor + FROZEN gold-blind telemetry (HorizonBench router lesson) +
     coefficient earns default via paired lift. Emitters map onto receipts' citation
     identity + Syndai active-read (WS-F); zero new tables.
   - **D-2026-08-09b — filter-before-track invariant → ADOPT.** "Suppressed/superseded read
     refreshes NO ranking counter" is unasserted today. $0 reader-free → pinned as a
     spec-31 golden.
   - **D-2026-08-09c — outcome-verified reversible injection block → CONDITIONAL on
     adherence Phase A.** OctoBench flat + veto dead → not a reason to build the lane. If
     Phase A opens it: admission outcome-verified (not frequency-ranked) + per-entry
     reversible. Three data-shapes borrowed onto existing lineage regardless.
2. **`docs/superpowers/specs/memphant/31-evidence-integrity-probes.md`** — the one spec delta
   (Mechanism 2, the only adopt): new `suppressed_read_no_refresh` metric + probe #5,
   verified by perturbation per the non-vacuity rule.
3. **`docs/superpowers/specs/memphant/13-prior-art-and-competitive-spec.md` §1.4a** — the
   prior-art paragraph (aidevops = live OSS peer on append-only + truth-maintenance +
   outcome-gated promotion on SQLite FTS5; not a substrate collision).
4. **Private Syndai mirror synced** — the same 3 files copied to
   `/Users/sidsharma/Syndai/docs/superpowers/specs/memphant/` because `check_spec_drift.py`
   requires byte-identical public/private. **If you edit any of these 3 docs again, re-sync
   the mirror or the drift gate fails.**

## Gate status

- `check_spec_drift.py` → **clean, exit 0** (after mirror sync).
- `check_evidence_contract.py` → **exit 0** (no promotion claim added that needs a contract).
- Rust/pytest/db-lint gates **not re-run** — prose-only edits don't touch them.

## Open / not done (deliberate)

- **Nothing committed.** Working tree has the 3 modified docs plus 3 untracked scripts
  from prior flow work (`scripts/adherence_bench.py`, `decision_loop_demo.sh`,
  `extract_decisions.py`) — confirm intent before committing; they predate this task.
- **Mechanism 2 is speced, not built.** The `suppressed_read_no_refresh` golden is a spec
  delta only. Building it = a spec-31 wiring task (add YAML cases + assert in the Rust
  contract test), $0, reader-free. Do it when the spec-31 suite next gets touched.
- **Mechanisms 1 & 3 are inert by design** — no action until their gates open (M1: dogfood
  telemetry volume; M3: adherence Phase A depth signal). Do not build either speculatively.

## Pointers

- Memory: `aidevops-memory-verdict.md` (indexed in MEMORY.md).
- Standing rules that governed the verdicts: `memphant-adherence-9team-synthesis`,
  `memphant-grep-beats-us` (Track-R bar), `memphant-lme-exposure-guard-gap` (frozen-cohort
  discipline), `memphant-golden-nonvacuity` (perturbation-verify probes).

## Initial prompt for next session

> Read `docs/handoff/2026-08-09-aidevops-mechanism-eval-handoff.md` and the memory
> `aidevops-memory-verdict.md` first. The aidevops 3-mechanism eval is done and docs-only;
> nothing is committed. Do NOT re-open the verdicts — they are recorded in doc 26 §9.
>
> Pick up exactly one of these, or commit-and-stop:
>
> 1. **Commit the deliverable.** Working tree has 3 modified docs (13/26/31) + their synced
>    private mirror in `../Syndai/...`, plus 3 untracked scripts predating this task
>    (`scripts/adherence_bench.py`, `decision_loop_demo.sh`, `extract_decisions.py`). Confirm
>    whether those scripts belong in this commit before staging. Run the full AGENTS.md gate
>    before claiming done. Branch is `codex/outcome-coupled-evolution`.
> 2. **Build the Mechanism-2 golden** (only if the spec-31 suite is being touched anyway).
>    Wire the `suppressed_read_no_refresh` probe: add YAML case(s) surfacing a
>    superseded + an `unresolved_contradiction`-suppressed unit, record each unit's
>    access/recency counters, recall, assert no counter moved; perturbation control =
>    remove the edge and assert the counter DOES move (non-vacuity). $0, reader-free, follows
>    the existing `evidence_integrity_*` add-a-probe path in doc 31 §3. This is a bug-catch
>    probe against existing suppression machinery, not a feature.
>
> Do NOT build Mechanism 1 (Q-value fold) or Mechanism 3 (outcome-gated injection block) —
> both are inert until their gates open (M1: dogfood telemetry volume floor on frozen
> gold-blind telemetry; M3: adherence Phase A depth signal). Building either speculatively
> violates the recorded decisions.
