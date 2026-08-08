# `user_facts` census (SC lane) — privacy preregistration

Date: 2026-08-05. Written and committed **before** the probe runs, following
`docs/build-log/2026-07-30-c1-replication-privacy-prereg.md`.

Purpose: answer §A.2 of the SC lane
(`2026-08-05-supersession-correctness-lane.md`) for `syndai.user_facts` — does
that table carry ≥60 supersede arcs and ≥60 coexist pairs? The C1 prereg refuses
"every other table in the `syndai` schema", so `user_facts` is **not** covered by
it and needs this document.

## Authorization

The repo owner explicitly authorized this exact operation in chat on 2026-08-05
("run the census on user_facts"), naming the table. `AGENTS.md` → *Sister Project
and Secrets* requires exactly that. This authorization covers **reads only**, and
this document narrows it further (below).

## This probe reads AGGREGATES ONLY — no row content, no PII

This is the material difference from the C1 extract, and it is deliberate. The
census question is a **counting** question, so it is answered with counting
queries. Therefore:

- The only statements issued are `SELECT` returning **aggregate values**:
  `count(*)`, `count(...) FILTER (...)`, `count(DISTINCT ...)`, `min/max` of
  timestamps, and a `GROUP BY category` **count**.
- **No `label` value, no `value` value, no `user_id`, no id of any kind is
  selected, printed, written to disk, or committed.** Not even redacted.
- No corpus file is produced. There is nothing to mirror, gitignore, or hash,
  because no row is read.
- Consequently no secret-scanning pass is needed: free text is never selected.

If the counts pass the §A.2 bar, extracting content becomes a **separate
operation** requiring its own preregistration, its own owner authorization, and
the full C1 machinery (secret scan, drop-whole-row, private mirror, redacted
lock). **This document does not authorize that**, and the run stops and reports
instead of proceeding into it.

## Access posture (binding, unchanged from C1)

- Source: `syndai` schema of Syndai production Postgres via
  `doppler run --project syndai --config prod`, the secret consumed only by the
  probe process, never printed or persisted.
- Every statement runs under `PGOPTIONS="-c default_transaction_read_only=on"`.
  Only `SELECT` is issued — no `INSERT`/`UPDATE`/`DELETE`/DDL/`VACUUM`/migration,
  no temp tables, no `SET` beyond the read-only guard.
- If any step would write, the run stops and reports rather than improvising.

## What is committed

Committed: this document, the probe SQL (mechanism only), and the resulting
**counts** in the SC lane's census log. Counts, rates, and date ranges only.

Never committed, and in this probe never even read: `label`, `value`, `user_id`,
`id`, `supersedes_fact_id` values, or any other row content.

## Spend

$0. The probe is a `SELECT` of aggregates. No paid provider call.
