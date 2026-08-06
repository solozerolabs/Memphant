# Memora / FAMA substrate-gain confirmation authorization packet (DRAFT — unsigned)

Date drafted: 2026-08-06. **Redesigned 2026-08-06 after `/plan-eng-review`**
(section review + Codex outside voice): the original "powered paired
confirmation vs the published best" framing was invalid — you cannot compute a
paired statistic against a published *scalar* with no question-level rows. This
packet is now an **internal paired substrate-gain** design. See the GSTACK
REVIEW REPORT at the end for the finding trail.

Status: **DRAFT for owner authorization. No paid call is authorized until the
signature block is filled and its `authorization_scope_sha256` is committed.**
Spend authorized so far: **$0.**

Axis 2 of the five-axis program
(`docs/superpowers/plans/2026-08-03-multi-axis-near-sota-program.md`). Axis 1
(HorizonBench) closed negative 2026-08-06. **Strategic honesty:** the five-axis
frontier claim is already unreachable (axis 1 failed). This run does not revive
it. Its only value is a valid, product-relevant datapoint — *does MemPhant's
memory substrate beat no-memory on forgetting-aware tasks, under a fixed
reader?* If no product or publication decision hinges on that, **shelving
(don't spend) is the correct call** — the dev candidate (FAMA 61.67) already
stands as development evidence.

## 1. Why this run exists

Memora/FAMA is qualified (`benchmarks/manifests/memora.lock.json`:
`geniesinc/Memora@a6493188`, Apache-2.0, strict three-judge). A dev candidate
scored FAMA 61.67 on the burned `weekly/software_engineer` group
(`docs/build-log/2026-07-15-memora-causal-split.md`). That is a single-arm,
single-group development result with **no baseline** — it cannot say whether the
memory substrate *caused* anything. This packet designs the controlled paired
comparison that can.

## 2. Claim boundary (what this can and cannot establish)

- **Can:** a **paired substrate-gain** result — MemPhant's memory substrate vs a
  no-memory baseline, the **same frozen reader** and the **same preregistered
  Memora sample**, scored by the official strict-three-judge FAMA, per task,
  with a real paired statistic over shared questions.
- **Cannot, and will not claim:** "near the Memora public frontier." The paper
  reports published **scalars** (six memory agents over their own LLMs) with no
  question-level rows to pair against, on an unreconciled scale, over the full
  600Q/27,614-session corpus. Those numbers are **descriptive context only**,
  never a paired comparator or a "within-5pp" target. Different systems top
  different tasks; there is no universal winner to be near.
- Per claim-contract §7 a fixed-reader comparison supports a **substrate-gain**
  claim only. That is exactly and only what this design targets.
- Synthetic-persona + LLM-judge limitations, and Memora's low mindshare
  (`[[memphant-benchmark-landscape-2026-07]]`), are reported. A substrate-gain
  win here is supporting evidence, not a headline.

## 3. Preconditions — all $0, all must pass before the signature block is valid

**P0. Build the paid-run harness + selector, and freeze the whole evaluated
system (code, no spend).** `scripts/run_memora_fama.py` today has no
authorization packet, no cost preflight, no spend ceiling, only a post-hoc judge
ledger (`open_campaign_ledger_from_env`), and only a single-group selector
(`select_group`). Required before any paid call:
- Port Horizon's `authorize / run-paid / analyze` harness + **packet-scoped
  ledger with a hard $ spend ceiling** (fail-safe stop) to the Memora runner.
  Extraction — the dominant cost — must be inside the ceiling, preflighted per
  session before spend.
- Add a **seeded, horizon-stratified, disjointness-asserting** multi-group
  selector (mirror `select-fresh-tranche`) that emits sample + exclusion hashes.
- **Freeze the entire generation stack in the packet**, not just the scorer:
  reader model, adapter, prompts, extraction/reflection model, recall budget,
  migrations aggregate, runner + server + worker binaries, and the exact runtime
  config — each hash-pinned *before* signing. Post-hoc repo hashes do not
  constrain what was authorized (Codex).
- Add **resolved-model fingerprinting**: record served provider/version/price
  per attempt (not just `requested_model`) for the reader and all three judges,
  and fail closed on drift. Mutable model names in the lock are not sufficient.
- Bind **generation integrity** in the scorer: link each answer + evidence to
  the frozen reader, recall trace, extraction bank, prompt, and authorization
  hash, and bind question text/date/task to the official rows before the
  evaluator substitutes sealed answers (`run_memora_fama.py:196`, `:338`).

**P1. Descriptive scale reconciliation ($0, non-gating).** Read the official
aggregation (`memory_to_answer.py`, `aggregate_results.py`) and, from the burned
group's already-paid judge rows, express our FAMA on the paper's scale **for
context only**. This no longer gates any claim (the claim is internal-paired),
so if it can't be reconciled the run still proceeds — the paper table is simply
omitted from the descriptive appendix.

**P2. Frozen reader for both arms ($0).** Pin one reader used identically by the
memory and baseline arms (the retained Nov-2025 `claude-opus-4-6` is the
default). **Language corrected:** this reader is *chronology-consistent* with a
pre-release checkpoint; it is **not proven uncontaminated** — predating the
arXiv date establishes chronology only, not training-set exclusion (Codex).
Report this as a caveat, not a guarantee. Verify resolvable (no spend).

**P3. Preregistered shared sample + real exposure provenance ($0).** Select the
sample with the P0 selector. Both arms run the **identical** question set.
Exclusion is **not** just the one burned group: build an exposure ledger =
union of every group/question inspected, debugged, or scored across the Memora
development history (July build-logs + causal-split), and assert the sample is
disjoint from all of it. Emit `sample_sha256` + `exposure_union_sha256`; fail
closed on any intersection.

**P4. Paired power + executable predicates ($0).** Power the **internal paired
delta** (a real paired statistic — the two arms share questions). Freeze
executable predicates with numbers, not adjectives: explicit non-inferiority /
superiority margin, one-sided α, tie rule, and minimum discordant pairs. Enough
question-clusters for credible clustered inference (4–6 groups is too few —
size to the predicate, not to convenience). Record
`benchmarks/manifests/instrument_power.json`.

## 4. Frozen inputs (pinned by P0–P4 before signing)

| input | value |
|---|---|
| benchmark | `geniesinc/Memora@a6493188…`, tree `12d63b7d…`, Apache-2.0 |
| native scorer | strict 3-judge (`claude-haiku-4.5`, `gemini-2.5-flash`, `gpt-4.1`), hashes per lock |
| generation stack | *[P0 — reader, adapter, prompts, extraction/reflection model, recall budget, migrations, binaries, runtime config, all hash-pinned]* |
| reader (both arms) | *[P2 — retained Nov-2025 `claude-opus-4-6`; chronology-consistent, contamination NOT proven]* |
| sample + exposure hashes | *[P3]* |
| power + predicates | *[P4]* |

## 5. Arms and exact configuration

**Two internal arms, paired per question, one frozen reader, one shared sample:**

| arm | evidence | note |
|---|---|---|
| `memphant_memory` | MemPhant packaged runtime, Fast recall, frozen 10-item/8,192-token budget, official info boundary | the substrate under test |
| `no_memory_baseline` | the same reader answering with no retrieved memory (or the minimal official baseline) | the control the dev candidate never had |

Official info boundary unchanged (observable dialogue only;
`session_type`/`operation`/`memory_evidence`/`forgetting_evidence`/`evaluation`
loaded only inside the scorer, post-answer). The paper's six agents are **not**
run — their published numbers are descriptive context (P1), never an arm.

## 6. Preregistered outcome predicates (frozen before scoring)

Paired, per-task, over the shared sample, group-clustered, **executable**:

1. **Primary (substrate gain):** paired FAMA delta (`memphant_memory` −
   `no_memory_baseline`) one-sided 95% LCB **> 0** overall, α = 0.05.
2. **Forgetting integrity (the FAMA-specific stratum):** obsolete-memory-use
   rate strictly lower in the memory arm; forgetting-absence not worse. Freeze
   the exact rate definitions in P4.
3. **Per-task:** report paired deltas + CIs for Remembering / Reasoning /
   Recommending separately; claim only the tasks that clear the P4 MDE.
4. **Power floor:** ≥ the P4 minimum discordant pairs per claimed task, else that
   task is inconclusive, not a pass.

Every margin, α, and tie rule is a frozen number (P4), not "noise band." Report
per-arm + paired FAMA, obsolete-use/forgetting rates, per-task deltas + CIs, the
descriptive paper context (P1, if reconciled), and the full UX rail (ingest +
extraction cost, stored bytes, recall/e2e p50/p95, prompt tokens, paid cost,
retries, unsettled liability).

## 7. Pilot kill gate (spend-limiting, optional-stopping-safe)

A pilot on a **pre-committed, representative** group (not the smallest — chosen
in P3 as part of the frozen sample). **Optional-stopping fix:** the pilot group
is a declared member of the final sample and its rows are reused verbatim in the
final result; the kill gate only *stops* on harm, it never *selects* on the
delta and never re-scores the pilot rows. Kill if: the memory arm's paired
pilot delta is negative beyond the P4 harm margin; obsolete-use rises in the
memory arm; reader or any judge resolved-fingerprint/price drift; incomplete
pricing / unsettled liability; extraction cost per session exceeds the P0 basis
+ buffer.

## 8. Cost preflight and ceiling

Basis (dev group): extraction ≈ $0.0143/session (dominant), reader + strict-judge
small. **Two arms** double the reader+judge calls but the baseline arm skips
extraction (no memory to build), so extraction is paid once per session.
Recompute at freeze from the P3 sample's session count through the P0 preflight.
Hard $ ceiling set at freeze, enforced by the P0 spend cap. Extraction bank is
content-addressed + resealable, so re-runs do not repay extraction.

## 9. Fail-closed conditions (any ⇒ stop, preserve partial evidence)

Reader or any-judge resolved provider/version/price drift (not just name);
incomplete/missing pricing; unsettled liability; spend ceiling reached; a
sample/exposure intersection; scorer source-hash mismatch; a generation-stack
hash not matching the authorized freeze; an answer reaching the scorer without a
verified reader/recall/extraction/prompt/authorization binding; the two arms
running a non-identical sample.

## 10. Evidence artifacts (committed; bodies stay out of git)

Under `docs/build-log/artifacts/memora-fama-substrate-gain/`: `sample.json`
(+ `sample_sha256`, `exposure_union_sha256`), `generation-stack-freeze.json`,
`power.json`, `authorization.json` (signed), extraction-bank identity + archive
sha, `reader-attempts.jsonl`, `judge-attempts.jsonl`, `answers.jsonl` (both
arms), `fama-scores.json`, `paired-analysis.json`, `closure.json`,
`result.json`. `result.json` binds source, sample, exposure ledger, full
generation stack, reader, scorer, extraction bank, repo tree, runner diff, and
tests by SHA-256. Dataset/extraction bodies + paid caches stay under the
protected cache root.

## 11. Post-run

Settle accounting; register the evidence contract; update **only** the Memora
row in `STATUS.md` and the program-plan portfolio row. A pass is a
**substrate-gain** datapoint on forgetting-aware tasks — not a Memora-frontier or
five-axis claim, both of which remain out of reach.

---

## Preconditions status

- [ ] **P0** harness + spend ceiling + seeded stratified selector + full generation-stack freeze + resolved-model drift detection + scorer generation-integrity binding — built and committed.
- [ ] **P1** descriptive scale reconciliation (non-gating).
- [ ] **P2** frozen reader for both arms; contamination caveat recorded (not "safe").
- [ ] **P3** preregistered shared sample + exposure-union provenance; disjointness asserted.
- [ ] **P4** paired power + executable predicates (margin/α/tie/min-discordant) frozen.

## Authorization signature block — UNSIGNED

- [ ] Design confirmed: **internal paired substrate-gain** (two arms, one reader, one shared sample). Paper numbers descriptive only.
- [ ] Reader pinned + resolvable; contamination caveat acknowledged.
- [ ] Sample + exposure ledger + power + predicates frozen and hash-pinned.
- [ ] Full generation stack frozen and hash-pinned before signing.
- [ ] Authorized ceiling (USD): *[set at freeze]*.
- [ ] Pilot kill gate armed (representative, optional-stopping-safe).
- [ ] Authorized by: *[owner signature]*
- [ ] `authorization_scope_sha256`: *[committed on signing]*

Paid execution runs **only** through
`doppler run --project syndai --config dev -- …`, stopping on any §9 condition.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_found | 3 section findings + 1 design fork, all folded; 0 critical gaps |
| Outside Voice | Codex (`model_reasoning_effort=high`) | Independent 2nd opinion | 1 | issues_found | 10 findings; "do not authorize as drafted"; converged on internal-paired redesign |

Section findings (all resolved via AskUserQuestion, all applied):
- **A1** — no paid-run authorization/preflight/spend-ceiling harness in `run_memora_fama.py` → **P0** ports Horizon's harness + hard ceiling.
- **A2** — no multi-group held-out selector (`select_group` is single-group) → **P0** adds a seeded stratified disjointness-asserting selector.
- **A3** — cross-reader confound makes "near-frontier" over-claim → reframed to substrate-gain.

Design fork (Codex-driven, resolved): the paired/powered-vs-published-scalar
framing was invalid (no question-level rows to pair). **Redesigned to an internal
paired baseline** (MemPhant vs no-memory, same reader, same sample). Codex's
other accepted findings folded in: exposure provenance beyond one group (P3),
pilot optional-stopping fix (§7), executable predicates (§6/P4), full
generation-stack freeze (P0/§4), resolved-model drift detection (P0/§9), P2
"contamination-safe" → "chronology-consistent" caveat, scorer generation-
integrity binding (P0).

Appendix (unverified, confidence ~5): 3-judge scorer determinism — if judges run
at temperature > 0, add a variance band; verify the scorer temperature during P0.

**CROSS-MODEL:** strong agreement, no tension — both reviewers independently
concluded substrate-gain is the honest ceiling; Codex additionally invalidated
the statistical framing, which reshaped the design.

**VERDICT:** ENG review complete — plan redesigned, not cleared-to-implement. P0–P4
remain open $0 preconditions; the strategic shelve-vs-run call (is any decision
worth the spend?) is the owner's before P0 work begins.

NO UNRESOLVED DECISIONS
