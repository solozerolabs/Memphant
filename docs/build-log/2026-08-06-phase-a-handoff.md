# Phase A repo-profile slice — session handoff (2026-08-06)

Read this + the plan of record (`docs/superpowers/plans/2026-08-05-adherence-plan.md`)
and the memory index. This session shipped the Phase A slice end to end. Nothing
below is speculative unless marked OPEN.

## The one-line state

MemPhant pivoted from a retrieval product to the **memory-backed adherence +
per-repo-profile layer for coding agents**. Phase A (per-repo runtime profile) is
**shipped to `Syndai/main`, deploying to `syndai-prod`, prod flag ON**. The
cohort's preregistered kill-clock starts at the first treated run on real traffic.

## What is DONE and verified

1. **Product call** (`docs/build-log/2026-08-05-what-coding-agents-need.md`): retrieval
   is not the constraint — 3 measurements (S4 grep 96.67% vs 58.89%; XS BM25 0.9091
   saturation; ~75% of what agents lacked was already in-context). The niche is
   adherence + per-repo runtime profiles + cross-repo scope + always-on trap block.
2. **Gates** before build: G1 veto-precision → WARN-ONLY (no rule ≥0.95); G1b
   state-aware → instrument invalid (labels circular), veto only via live shadow;
   G2 external-validation kit built (`benchmarks/xs_crosssession/g2_kit/`) — **NOT
   YET RUN, needs 2-3 non-Sid users**; G3 model-replay → 0/21 recurrence (short
   context; niche durability rests on HANDBOOK.md 36.2% + omission-decay, not this).
3. **MemPhant prod cell**: Fly `memphant-prod`, private 6PN only
   (`http://memphant-prod.internal:3000`), server+worker up, BYOC `memphant` schema
   in Finn Supabase, `MEMPHANT_EMBEDDINGS=off` on this cell (Phase A is deterministic
   facts; dense channel unused — bake the model into the image before any vector
   feature). Bring-up gotchas in `docs/deployment/prod-cell-runbook.md`.
4. **The Phase A slice** (Syndai branch `memphant-repo-profile`, MERGED to main):
   finalize captures 2 deterministic facts (`sandbox_backend`, `resource_tier`) as
   active `semantic` units via a dedicated `syndai:repo-profiler` SYSTEM actor; a
   bounded block renders into the coding executor's turn-1 prompt; `repo_profile_sha`
   marks treated runs. Proven end to end against the live store (block renders,
   A-B-A supersession works). Flag `MEMPHANT_REPO_PROFILE_ENABLED` ON in syndai/prod
   doppler; `MEMPHANT_API_BASE_URL`/`MEMPHANT_API_KEY` set.
5. **Review**: 3 adversarial rounds converged 9→2→0. Every finding fixed or
   dispositioned in `.preflight-state/findings-ledger.md` (Syndai branch).

## Key files

| what | where |
|---|---|
| Plan of record | `Memphant docs/superpowers/plans/2026-08-05-adherence-plan.md` |
| Slice: write/read | `Syndai backend/src/features/memory/repo_profile.py`, `memphant_repo_profile_adapter.py` |
| Slice: inject seam | `Syndai .../engine_loop/activities_coding_executor_support.py` (`apply_turn1_repo_profile`) |
| Slice: capture | `Syndai .../engine_loop/activities_coding_finalize_helpers.py` (`_capture_repo_profile_after_finalize`) |
| Cohort prereg | `Memphant docs/build-log/2026-08-05-phase-a-cohort-prereg.md` |
| Cell runbook | `Memphant docs/deployment/prod-cell-runbook.md` |
| Dev stack | `Memphant scripts/dev_stack.sh` (local: server 127.0.0.1:3020 + worker, dev tenant) |
| Prod cell creds | `~/.memphant-private/prod-cell/login_roles.env` |

## BLOCKER (2026-08-06) — Syndai/main is RED, deploy failed, NOT our slice

The Phase A slice is correctly merged to main, but the Deploy **failed** and Phase A
is **not on prod**. Cause is pre-existing and unrelated: **the postbag `tags`+segments
feature left main red.** `backend/src/features/newsletter/service.py:547`
(`tags=frozenset(row["tags"] or ())`) KeyErrors because the tick-routing test's row
has no `tags` key. **Proven not-ours:** the test fails at commit `852332984` (postbag),
which predates the slice; the last 4 consecutive main Deploys all failed on it
(`652030d9`/`7831e0d9`=our push/`66c0ed51`/`0776ce59`).

**Decision (owner, 2026-08-06): routed to the postbag owner — do NOT fix their
feature.** The slice deploys automatically on the next green main. Fix is either
`row.get("tags")` (reader tolerance) or the row-builder should supply `tags` — the
postbag owner's invariant to decide. **Next session: check if main is green yet
(`gh run list --repo solozerolabs/Syndai --branch main --workflow Deploy --limit 1`);
if green, the slice deployed — jump to "confirm treated run #1" below. If still red,
it is still the postbag blocker, still not ours.**

## OPEN — next session, in priority order

1. **Watch the deploy land once main is green** (see BLOCKER above — currently red on
   postbag, not us). `gh run list --repo solozerolabs/Syndai --branch main` (poll
   ≥120s — abuse limiter, see LEARNINGS `github-api-poll-interval`). If a NEW failure
   appears that IS in our files (repo_profile/coding engine_loop/memphant adapter),
   that is ours; the postbag `tags` one is not. On deploy failure read
   `flyctl logs -a syndai-prod`; bluegreen means a health-check timeout leaves old
   machines serving.
2. **Confirm treated run #1**: after deploy, the first two coding runs on one repo
   should produce a `repo_profile_sha` on run 2. Query prod:
   `select count(*) from syndai.coding_execution_attempts where executor_metadata ?
   'repo_profile_sha'` (aggregate only). Then let the cohort accrue to n≥30 before
   the kill-clock verdict (repair-turns/cost/latency, flag-on vs off).
3. **G2 external validation** — the one thing that can still demote the whole
   thesis. The kit is built and privacy-clean; needs 2-3 friendly non-Sid Claude
   Code users to run `g2_kit/g2_miner.py` and send back `g2_result.json` (counts
   only). Decision rule preregistered: pooled `already_written_share < 0.40` ⇒
   adherence demotes to a Syndai-internal feature.
4. **Before trusting the cohort at scale**: full post-commit relocation of capture
   (currently reads on `fresh_session_from_bind` + `wait_for(8s)` bound; residual is
   ≤8s FOR UPDATE hold because the caller loops in one shared session). Deferred as
   YAGNI for a flag-gated path — do it before wide enablement. See LEARNINGS
   `finalize-besteffort-io-off-request-session`.
5. **Defense-in-depth follow-ups** (not blocking): `_subject_of` predicate-aware
   parse (colon-in-predicate spoof, not independently exploitable); MemPhant-side
   per-key actor allowlist so system-trust can't be claimed for arbitrary actors.

## Standing rules that bit this session (don't relearn them)

- **`flyctl deploy | tail` masks the exit code** — always `> log 2>&1; echo $?`.
- Fly remote builds: trixie images (ONNX needs glibc ≥2.38), `MEMPHANT_BIND=[::]:3000`
  (6PN is IPv6), watch for lease-timeout-leaves-machine-stopped.
- Preflight: every `run.sh` rebases and re-shas HEAD, staling phase marks; and any
  shipping-code change re-opens phase 4. Batch fixes, mark at the settled HEAD.
- A best-effort HTTP writer in the finalize path must NOT hold the request session
  across HTTP (aliased FOR UPDATE; `check-io-in-locked-tx` can't see it).

## What NOT to do

- Do not build more retrieval recall benches (measured dead: grep/BM25 saturate).
- Do not build the Claude Code plugin / MCP hook surface until Phase A cohort
  reports AND G2 validates — that's plan Phase B, gated on Phase A + G2.
- Do not flip the subject-resolution flag or build supersession machinery — no
  instrument exists (all 4 corpora rejected; SC lane closed).
