# Track U (user-learning golden bank) — privacy preregistration

Date: 2026-07-30. Written and committed **before** any extraction run, per the
binding requirement in `docs/superpowers/plans/2026-07-27-accuracy-first-program.md`
(Phase 1a-U, "Privacy preregistration (before mining)").

Phase 1a-U mines a 40–60 golden user-learning bank from material the repo owner
personally owns: his own agent-memory corpus. That material is private and stays
private. This document fixes the rules before the data is touched, so no
after-the-fact judgement decides what may be published.

## Sources (read-only, exact paths)

1. `~/.claude/projects/*/memory/feedback_*.md` — the owner's per-project agent
   feedback memories.
2. `/Users/sidsharma/Syndai-memphant-ref/LEARNINGS.md` — a clean Syndai worktree
   at `main`, provided read-only for this phase.
3. Hard-rule sections of `AGENTS.md` in both repos:
   - `/Users/sidsharma/Syndai-memphant-ref/AGENTS.md` (`## Session Execution`,
     `## Hard Rules`)
   - this repo's `AGENTS.md` (`## Repo Boundaries`, `## Sister Project and
     Secrets`, `## Database Rules`, `## Working Rules`, `## CI monitoring`)

No other source is admitted into the first slice. Nothing is written to any of
them: the extractor opens every source read-only and never creates, edits, or
deletes a file under `~/.claude/projects/` or in the Syndai worktree.

## Counts pinned at prereg time (observed 2026-07-30, re-pinned by the lock)

The plan's "~60 verified" `feedback_*` figure is stale. Observed now:

| Source | Count |
|---|---|
| `feedback_*.md` total | **90** |
| ↳ `-Users-sidsharma-Syndai/memory` | 67 |
| ↳ `-Users-sidsharma-Yurivan/memory` | 14 |
| ↳ `-Users-sidsharma-Ideas/memory` | 3 |
| ↳ `-Users-sidsharma-Ideas-tacitry/memory` | 3 |
| ↳ `-Users-sidsharma-sprntly/memory` | 2 |
| ↳ `-Users-sidsharma-Namera/memory` | 1 |
| Syndai `LEARNINGS.md` one-line entries | **61** |
| Syndai `AGENTS.md` `## Hard Rules` bullets | 17 |
| Syndai `AGENTS.md` `## Session Execution` bullets | 9 |
| MemPhant `AGENTS.md` rule bullets (5 sections) | 25 |

The extractor recomputes every one of these at run time and writes them into the
committed lock file. If an observed count differs from the number above, the LOCK
is authoritative and the difference is a corpus change, not an error to be
papered over.

## What is committed and what is not

Committed:

- `scripts/user_lane_extract.py` — mechanism only. No rule text, no incident
  text, no probe text.
- `benchmarks/data/user_lane_golden.lock.json` — sha256 + byte size + counts +
  per-axis / per-category / per-scope strata + parameters + accept/reject stats +
  source-file counts. **Counts and hashes only, never content.**
- this document.

Never committed (gitignored):

- `benchmarks/data/user_lane_golden.jsonl` — the bank bodies (rule, incident,
  how-to-apply, probe prompts, expected behaviors).
- `benchmarks/data/user_lane_probes.jsonl` — the authored probe layer (temptation
  prompts, expected observable behavior, adjudication verdicts). It paraphrases
  private memory content and is therefore treated exactly like a body.

Neither file may be pasted into a commit message, a build-log document, a STATUS
entry, an issue, or a report. Reports quote strata and statistics only.

## External-claim rule (binding)

Any external claim derived from this bank — including a published
correction-retention number, a blog figure, a leaderboard submission, or a README
line — requires a **paraphrase-scrubbed or synthetic public-reproducible
variant** of the bank, re-adjudicated to the same preregistered bar, and the
number must be recomputed on that variant. The private bank's own numbers are
internal decision evidence only. A private-bank number is never published, not
even "for illustration", and not even rounded.

Scrubbing bar for the public variant, when it is built: no repo names, no project
refs, no user handles, no file paths, no session ids, no dates tied to an
incident, no verbatim sentence longer than a clause from any source. Its
adjudication runs the same accept checks as the private bank and its own lock is
committed.

## Content exclusions

Two `feedback_*` files in the `-Users-sidsharma-Yurivan` project encode adult-
content tagging vocabulary. They are valid semantic-memory material but are
excluded from the bank so that no derivative artifact — private bank, probe file,
or future public variant — carries that content. The exclusion is recorded as a
reject reason (`content_sensitive_excluded`) in the lock, not silently dropped.

## Spend

$0 paid spend. Adjudication runs on subscription-model agent calls. The extractor
makes no network call and no provider call at all; it is pure local parsing plus
validation, so a rerun is free and byte-deterministic.

## Durability of the gitignored inputs (added 2026-07-30, integration)

The authored probe layer is **human-authored and irreplaceable**: the extractor
is deterministic, but it derives the bank *from* the probes, so the lock verifies
nothing without them. Gitignored-and-single-copy is exactly how this repo
previously lost the ~64k-event local code-lane corpus, so the probe layer and the
bank are mirrored outside every worktree at `~/.memphant-private/track-u/`
(outside any git repository, never committed, never published):

| file | sha256 |
|---|---|
| `user_lane_probes.jsonl` | `dff3b42903121a8a40998b5bf172e3b827f5ca0a8520cfbc11599465b1ef1a6a` |
| `user_lane_golden.jsonl` | `e29821b24aff753a3bd5f58653f65044851485d1a7690d1081dedcb1a5200b87` |
| `user_lane_golden.lock.json` | `13e32d160eda5dfeeae5be6ce0229401285265a79c99314df9a72bb3a99a6c70` |

The bank hash matches `benchmarks/data/user_lane_golden.lock.json`, so the mirror
is verifiable against the committed lock without exposing any content. Recording
the hashes here keeps the committed record sufficient to detect tampering or
drift in the mirror; it does not make the private material recoverable from the
repository, which is the intent.

This does not weaken the privacy posture: the mirror is local-disk-only, and the
external-claim rule above is unchanged — any published number still requires the
paraphrase-scrubbed or synthetic public variant, re-adjudicated to the same bar.

## Source snapshot pinning (added 2026-07-30, after a live-drift failure)

The bank was extracted from the **live** `~/.claude/projects/*/memory/` tree. That
tree mutates: on 2026-07-30 a concurrent Syndai session wrote
`feedback_guard_the_door_the_callers_actually_use.md`, moving
`feedback_files_total` **90 → 91** and failing `--check` on a bank nobody had
touched. An eval corpus that drifts under its own lock is not an instrument.

Extraction is now pinned to a frozen snapshot at
`~/.memphant-private/track-u/sources/` (94 files: 91 `feedback_*`, plus
`LEARNINGS.md` and both `AGENTS.md`), hash-recorded in
`~/.memphant-private/track-u/sources.manifest.json` with
`snapshot_sha256 = 5501ab7e12f7591805537502c6249c635a889aca6310b9e70116c41101a2fbc6`.
The lock now carries a `source_snapshot` block, so it pins its own inputs instead
of trusting whatever the live tree happens to hold; if no snapshot is present the
block records `pinned: false` rather than silently reading live.

**The drift was inert with respect to bank content**: re-cutting against the
snapshot reproduced the bank byte-for-byte at `sha256 = e29821b24aff…`, with only
the recorded source count moving 90 → 91. The 51 goldens, all strata, and all
reject reasons are unchanged.

Privacy posture is unchanged: the snapshot lives outside every worktree and every
git repository, is never committed, and the external-claim rule still requires a
paraphrase-scrubbed or synthetic public variant.
