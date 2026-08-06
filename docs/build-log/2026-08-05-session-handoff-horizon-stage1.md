# Session handoff — HorizonBench evolving-preference axis, stage-1 paid run

**Written:** 2026-08-05. **Repo state at handoff:** `origin/main` @ `c90d0201`
(everything below is landed and pushed). **Next real action:** authorize +
run the **stage-1 interim** paid confirmation (60 users / 120 items, ~$109,
within the $140 ceiling).

---

## TL;DR

We repaired the evolving-preference axis's binding defect, proved the fix
generalizes to fresh users for free, fixed the paid-ledger fragility the pilot
exposed, and ran a real $18.95 paid pilot **kill gate that passed**. The
group-sequential paid study is signed and armed. The only thing left before the
axis produces decisional evidence is to run the interim (stage 1) and then,
conditionally, extend to n_max. Everything up to the interim's first paid call
is done; the interim run is the next decision.

## The axis and the story so far

**Axis:** evolving preference / belief state — HorizonBench (external ref 52.8%
overall). The 2026-08-03 powered confirmation (v7) had Fast at **−15.8pp** vs
full context and we stopped the lane.

**Root cause we found (this program):** that v7 run had
`MEMPHANT_FACT_EXTRACTION=0`, so supersession was structurally unreachable
(1,448/1,448 evidence units auto-keyed, 0 closed generations). And even with
extraction on, the subject key was `{scope}:{family}:{subject_phrase}` — *lexical
phrase identity* — so a preference restated in different words never superseded
the belief it replaced. Full detail:
`docs/build-log/2026-08-05-horizon-stage1-supersession-defect.md`.

**The fix (landed):** semantic subject identity —
`MemoryService::with_subject_resolution_threshold` /
`MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD` / `--subject-resolution`, **default OFF**,
**calibrated 0.85**. Before admission a mined candidate whose subject phrase is
cosine-near an open unit's adopts that unit's `fact_key`, and the existing
subject-key supersedence machinery closes the generation. A one-subject-per-job
guard (seeded with the keys the job will *derive*) prevents same-job collisions
against the Postgres exclusion constraint — pinned by
`crates/memphant-store-postgres/tests/subject_resolution_pg.rs` (InMemoryStore
cannot see the constraint; store-divergence trap). Chat-lane non-inferiority
holds on the frozen LME-S dev split (recall@5 unchanged, recall@10
0.8012→0.8072, zero per-question losses).

**Free proof it generalizes (P3):** on the fresh 60-user interim with the fix
on, supersession fired **398 times / 796 edges / 398 closed generations** on
users the fix had never touched (`p3-supersession-evidence.json`).

**Paid pilot kill gate — PASSED ($18.95 real spend):** 10 users / 20 items,
first-party `claude-opus-4-6`. Fast **40.0%** (8/20) vs v7 Fast 36.7% — the fix
did **not** make Fast worse; the Fast-vs-full gap shrank from −15.8pp to −5pp on
this tiny sample. **n=20 is noise, not a result** — the gate only checks
not-catastrophically-worse, and it cleared. Settled in `pilot-settlement.json`.

**Paid-ledger robustness fix (landed):** the pilot exposed that a single
transient network error (retried to success) left an error row in the
append-only journal that blocked campaign closure *after* the money was spent.
`scripts/provider_attempts.py` now tolerates a transient error superseded by a
priced retry of the same `request_key`, without weakening the one-charge-per-
request guarantee (`tests/test_provider_attempt_ledger_contract.py`, 3 new
tests). **This is why stage-1 (240 calls) and n_max (400 calls) can now close** —
at that call count a transient error is near-certain.

## The plan of record (authoritative)

- **Packet (signed):**
  `docs/superpowers/plans/2026-08-05-horizonbench-fresh-tranche-authorization.md`.
  Preconditions P1–P4 all `[x]`; owner authorization signed (Sid Sharma, chat
  2026-08-05); `authorization_scope_sha256`
  `f211d491609a2ec16dbdbcc117bbf35c2ebefa16af605987b45a8f9d5d813dd3`.
- **Program:**
  `docs/superpowers/plans/2026-08-03-multi-axis-near-sota-program.md` (five-axis
  claim contract; this is one axis).
- **Ledger:** `docs/superpowers/specs/memphant/STATUS.md`.

**Design elected: group-sequential.** n₁ = **60u/120i** (interim, a *look*, not
a decision) → n_max = **102u/204i** (34/gen). O'Brien–Fleming, 2 looks,
information fractions {0.5882, 1.0}, two-sided α = 0.05; interim nominal α₁ =
**0.0106** (crit z₁ = 2.556). The interim can stop early only for *overwhelming*
efficacy; stop-for-harm is the pilot kill gate's job (already cleared).
Power (P4): interim n=120 MDE 13.4pt vs the 10.8pt decision gap ⇒ interim is a
look; required_n = 183 items ⇒ the 102u/204i n_max is adequate.

**Ceilings (authorized):** stage 1 **$140**, combined **$260**. Reader pinned:
`claude-opus-4-6`, first-party Anthropic, **1M-context route required**
(full-context prompts reach ~358k tokens — several exceed 200k, the exact v7
OpenRouter failure; v7 proved opus-4-6 handles this first-party), uncached,
structured JSON, 1,024-token output cap.

## THE NEXT STEP — run the stage-1 interim (60u/120i)

Frozen inputs already committed under
`docs/build-log/artifacts/horizonbench-fresh-v1/`:
- `fresh-selection-20.json` — the 60-user interim selection (seed
  `horizonbench-fresh-v1`, disjoint from the 77-user burn set, nests the pilot
  and inside n_max).
- Body files (`fresh-source-20.jsonl`, evidence, caches) are **gitignored** —
  regenerate them; they are deterministic from the selection + pinned corpus.

Command flow (all through `doppler run --project syndai --config dev`; stop on
any §9 fail-closed condition):

1. **Build interim Fast evidence** (fix on), ~25 min for 60 users (see perf
   note), on a scratch DB you keep:
   ```
   MEMPHANT_SCRATCH_ACTIVE=1 DATABASE_URL=<scratch> \
   MEMPHANT_FACT_EXTRACTION=1 MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD=0.85 \
   python3 scripts/run_horizonbench.py build-confirmation-evidence \
     --source <regenerated fresh-source-20.jsonl> \
     --selection docs/build-log/artifacts/horizonbench-fresh-v1/fresh-selection-20.json \
     --out <interim-fast-evidence.jsonl> --report-out <interim-fast-gate.json> \
     --database-url <scratch> --port <p>
   ```
   (Regenerate `fresh-source-20.jsonl` with `select-fresh-tranche
   --users-per-generator 20 --extra-exclusions <date-integrity-exclusions.json>`;
   needs `uv run --with pyarrow`.)

2. **Authorize** the interim confirmation. Cost preflight needs a
   tokens-per-char basis. Two options:
   - Bootstrap (reproducible, no gitignored deps):
     `--pilot-prompt-chars 5673862 --pilot-prompt-tokens 1379099` (v7's banked
     ratio 0.24306).
   - Or the pilot's real rows if `paid-rows.jsonl` is still present locally.
   ```
   python3 scripts/run_horizonbench.py authorize-confirmation \
     --source <fresh-source-20.jsonl> \
     --selection .../fresh-selection-20.json \
     --fast-evidence <interim-fast-evidence.jsonl> --fast-gate <interim-fast-gate.json> \
     --pilot-prompt-chars 5673862 --pilot-prompt-tokens 1379099 \
     --authorized-by "Sid Sharma (chat approval 2026-08-05)" \
     --authorized-at "2026-08-05T00:00:00-07:00" \
     --out <interim-authorization.json>
   ```
   Projected cost ~$109 < $140 ⇒ preflight passes.

3. **Fire** (this spends ~$109). `run_paid_confirmation` validates the packet
   *before* any reader call, so a bad packet spends $0:
   ```
   doppler run --project syndai --config dev -- python3 scripts/run_horizonbench.py \
     run-paid-confirmation --source <fresh-source-20.jsonl> \
     --selection .../fresh-selection-20.json \
     --fast-evidence <interim-fast-evidence.jsonl> --fast-gate <interim-fast-gate.json> \
     --authorization <interim-authorization.json> \
     --output <paths from packet execution.raw_rows>
   ```

4. **Analyze + apply the interim OBF stop rule.** Score paired Fast vs full
   (answer vs quarantined `correct_letter`). The interim can decisively *stop
   for efficacy* only if p < **0.0106**; otherwise **proceed to n_max** (extend
   to 102u/204i, reusing the interim's reader cache for its first 60 users so
   the extension pays only the ~$73 increment, within the combined $260). Report
   the full UX rail (ingest cost, stored bytes, recall/e2e p50/p95, prompt
   tokens, paid cost, retries, unsettled liability).

## Discipline / gotchas — read before touching anything

- **Exposure guard.** Never touch the burned users (60 v7 confirmation + 10
  pilot + 2 drift + 6 date-integrity = 77) or the LME-S 238 live-exposure set.
  The fresh selection is hash-bound; do not re-cut any split constant or re-seed.
- **Reader must be first-party `claude-opus-4-6` on the 1M route.** OpenRouter's
  200k-capped endpoint 400s on the full-context arm (v7's first failure). The
  reader path sets no explicit 1M beta header — it relies on opus-4-6's native
  first-party window, which v7 proved (240/240, 0 errors).
- **Perf.** Subject resolution reads + embeds the whole open scope on every
  compile, so build time grows with scope size (the 60-user P3 gate took ~25
  min / 9513 episodes). This will show in the paid run's latency/cost rail and
  is a candidate optimization (cache scope embeddings, or bound the comparison
  set) before scaling further.
- **Gitignored bodies/caches.** `fresh-source-*.jsonl`, `*-fast-evidence*.jsonl`,
  `pilot-source-*.jsonl`, `reader-cache/`, `reader-attempts.jsonl`,
  `paid-rows.jsonl` are all out of git (dataset bodies / prompt-echoing caches).
  A fresh clone regenerates evidence deterministically but will NOT have the
  pilot's reader-cache, so the pilot's 20 items won't be free on a fresh machine
  (they ran under a different authorization scope hash anyway).
- **Shared-worktree hazard (live this session).** `/Users/sidsharma/Memphant`
  has had concurrent sessions on other branches with uncommitted `store.rs`
  edits. Branch into your own worktree before editing; do not switch the main
  worktree's checkout out from under another session; ff-only, never force-push.
- **Not decisional / not SOTA.** Nothing here supports a HorizonBench or
  cross-axis SOTA claim. The five-axis claim needs every axis independently
  (program plan §Required Portfolio). This axis produces *development* evidence
  only until a full official protocol passes.

## Open items / risks

- **Two pre-existing evidence-contract test failures were fixed** on this branch
  (fresh-v1 artifacts registered). If they resurface after a rebase, rerun
  `python3 scripts/check_evidence_contract.py --report` and re-register.
- **The interim is a look, not a decision** — do not over-read a n=120 result.
  Only the n_max (204i) design is powered for the ±5pp non-inferiority claim.
- **Cross-axis:** the other four axes (Memora/FAMA, RHELM, LongMemEval-V2/AFTER,
  RepoMem) are untouched by this work; free qualification gates come first there.

## Cost ledger (this axis, to date)

| item | spend |
|---|---|
| all $0 free work (P1–P4, pilot construction, dry-run) | $0 |
| paid pilot kill gate (10u/20i, 40 calls) | **$18.95** |
| — remaining under stage-1 $140 ceiling | ~$121 |
| projected stage-1 interim (60u/120i) | ~$109 |
| projected n_max extension increment (cache-reuse) | ~$73 |
| combined ceiling | $260 |

## Key commits (all on `origin/main` ≤ `c90d0201`)

`4e4a19eb` subject identity · `a2c5bb86` collision guard · `f3d13163` ledger
transient-retry fix · `61fd0c57` P1/P2/P4 · `93099546` P3 pass · `01291ade`
signed authorization · `0028e8cd` pilot armed · `c7bad011` bootstrap authorize ·
`3ae556e4` pilot passed + settle · `032f55f6` evidence-contract registration ·
`c90d0201` merge to main.
