# C1 replication (W9) — privacy preregistration

Date: 2026-07-30. Written and committed **before** the extraction run, following
the pattern fixed by `docs/build-log/2026-07-30-track-u-privacy-prereg.md` and
`docs/build-log/2026-07-31-convo-lane-bar-and-privacy.md`.

Purpose: re-extract the C1 episodic slice from Syndai production so Bars 1–3 can
be re-measured on the current build. The original extract
(commit `6d01789b`, 2026-07-22) was a one-time gitignored corpus and is no longer
on disk, so C1's real-data arm is currently unreproducible. This document fixes
the rules before the data is touched.

## Authorization

The repo owner explicitly authorized this exact operation (a read-only
production extract for the C1 replication). `AGENTS.md` → *Sister Project and
Secrets*: "must never target a Syndai production database unless the user
explicitly authorizes that exact operation". This is that authorization, and it
covers **reads only**.

## Access posture (binding)

- Source: the `syndai` schema of the Syndai production Postgres, reached via
  `doppler run --project syndai --config prod` with the secret consumed only by
  the extractor process. No Doppler value is printed, copied, or persisted.
- **Every** statement runs under `PGOPTIONS="-c default_transaction_read_only=on"`.
  Only `SELECT` is issued. No `INSERT`/`UPDATE`/`DELETE`/`DDL`/`VACUUM`/migration,
  no temp tables, no `SET` other than the read-only guard.
- If any step would write, the run stops and reports instead of improvising.
- The MemPhant bench itself never touches prod: it runs against an ephemeral
  scratch database minted by `scripts/with_scratch_db.sh`.

## Snapshot pinning

Live sources mutate — the C1 row count has already moved 252 → 270 → 321 across
three observations. Extraction therefore writes **one frozen snapshot**, hashes
it, and every downstream measurement reads the snapshot, never the live table.
The snapshot's `sha256` is recorded in the committed lock and in this document
after the run.

## What is committed and what is not

Committed:

- `scripts/c1_prod_extract.py` — mechanism only. No row content, no identifiers.
- `benchmarks/data/c1_prod_episodic.lock.json` — counts, strata, per-tenant
  breakdown with `user_id` **redacted to an 8-hex prefix**, sha256 + byte size of
  the corpus and of the snapshot, extraction parameters, secret-scan reject
  stats. **Counts and hashes only, never content.**
- this document, and the build-log write-up of the run.

Never committed (gitignored under `benchmarks/data/private/`):

- `benchmarks/data/private/c1_prod_episodic.jsonl` — the row bodies.

No body text, summary text, full `user_id`, `l0_agent_id`, `project_id`,
`mission_id`, or `idempotency_key` may appear in a commit message, a build-log
document, a STATUS entry, an artifact under `docs/build-log/artifacts/`, or a
report. Published artifacts carry counts, rates, latencies, and 8-hex tenant
prefixes only — the same redaction the 2026-07-22 artifact used.

## Fields extracted, and fields refused

Extracted (the columns the backfill mapping and the Conversations-tab DTO read):
`id`, `user_id`, `l0_agent_id`, `project_id`, `mission_id`, `content`,
`source_kind`, `importance_score`, `trust_level`, `tainted`, `rolled_up`,
`archived_at`, `created_at`, `idempotency_key`.

Refused outright:

- `embedding` — a stale vector from another model; useless here and bulky.
- `metadata` (jsonb) — free-form agent state, the likeliest carrier of tokens,
  headers, and internal URLs. Not read, not extracted.
- `summary` — redundant with `content` and doubles the private surface.
- every other table in the `syndai` schema.

## Secrets

Production conversation text will contain credentials. The extractor scans each
candidate row and **drops the whole row** on a hit — it never redacts in place,
because a partially scrubbed secret is still a leaked secret and a redacted body
is no longer the real distribution. Patterns: OpenAI/Anthropic/Doppler/GitHub/
Slack/AWS key shapes, `Bearer` tokens, JWTs, private-key PEM headers, and
`postgres://`/`postgresql://`/`redis://` URIs carrying a password. Drops are
counted by pattern class in the lock; the dropped bodies are not written
anywhere.

## Mirror

The corpus is mirrored to `~/.memphant-private/c1/` (outside every worktree and
every git repository) with its sha256 recorded in this document after the run, so
the 2026-07-22 loss — a gitignored single copy that vanished — is not repeated.

## External-claim rule (binding)

Any external claim derived from this slice requires a synthetic or
paraphrase-scrubbed public-reproducible variant, re-derived to the same bar, with
the number recomputed on that variant. Numbers from the private slice are
internal decision evidence only. Latency and correctness **rates** may be
reported internally; row content never leaves the private root.

## Spend

$0 paid spend. The extract is a `SELECT`. The bench is deterministic retrieval on
a local scratch database with a local embedder. No OpenRouter call, no paid
provider call, at any point.

## Counts pinned at prereg time

Observed 2026-07-30 by read-only `SELECT` against production, before extraction.
The extractor recomputes each of these and writes them to the lock; if a number
differs, the lock is authoritative and the difference is corpus drift, not an
error to paper over.

| Quantity | Value |
|---|---|
| `syndai.episodic_memories` rows | **321** |
| distinct `user_id` | 5 |
| `rolled_up = true` | 277 |
| `rolled_up = false` (recall-visible candidates) | **44** |
| `dialog_turn` / rolled-up | 263 |
| `dialog_turn` / live | 29 |
| `rollup` / live | 15 |
| `rollup` / rolled-up | 14 |
| `run_message_id` non-null | **0** |
| `syndai.run_messages` rows | 365 (`user` 191, `assistant` 174) |
| distinct `user` message bodies | **63** of 191 |

Per-tenant (prefix-redacted), total / live: `d6f83507` 246/24 · `cd67a3b2` 61/6 ·
`5f0721e7` 9/9 · `eee527c5` 4/4 · `d13f7632` 1/1.

## Scope limit stated in advance

This prereg authorizes an extract sized for **Bars 1–3** (hot-path SLO,
state-filter exactness, RLS). It does **not** authorize widening the extract to
other tables in pursuit of a larger golden bank. If the slice turns out to be too
small to mint a paired golden bank — which the counts above already suggest — the
answer is to report that, not to extract more of production.
