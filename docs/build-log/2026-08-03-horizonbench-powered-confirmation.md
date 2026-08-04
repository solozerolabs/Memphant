# HorizonBench powered confirmation

Date: 2026-08-03
Outcome: negative; stop

## What closed

The pinned free census and construction gate selected 60 held-out users across
the three generators, with one static and one evolved item per user. Fast
retained and compiled 8,875 episodes and produced 120/120 non-degraded evidence
rows before any paid reader call.

The final clean run used first-party Anthropic `claude-opus-4-6` for both arms,
independent uncached prompts, a 1,024-token output cap, and native structured
JSON. It closed 240/240 terminal rows with 240 priced attempts, zero errors,
zero retries, $109.1346 settled cost, and zero unsettled liability.

## Result

| Measure | Full context | Fast | Fast minus full |
|---|---:|---:|---:|
| Overall | 63/120 (52.5%) | 44/120 (36.7%) | -15.8pp |
| Evolved | 27/60 (45.0%) | 21/60 (35.0%) | -10.0pp |
| Static | 36/60 (60.0%) | 23/60 (38.3%) | -21.7pp |
| Evolved distractor selections | 10 | 17 | +7 |

The paired user-cluster bootstrap 95% interval for the overall delta was
[-24.2pp, -7.5pp]. The 31 discordant pairs comprised six Fast gains and 25
losses. Fast failed overall non-inferiority, positive evolved lift, and
distractor non-regression; only the discordance floor passed.

## Boundary and lineage

This is decisional negative evidence for the held-out 60-user tranche. It is
not an official-complete HorizonBench score and cannot support a HorizonBench,
overall-SOTA, or cross-axis near-SOTA claim. The selected tranche is burned:
it may be analyzed, but never tuned on or relabeled as held out. The official
4,245-item treatment, router work on these users, and Deep are unauthorized.

Canonical machine evidence:

- `docs/build-log/artifacts/horizonbench-confirmation-v7/authorization.json`
- `docs/build-log/artifacts/horizonbench-confirmation-v7/paid-census.json`
- `docs/build-log/artifacts/horizonbench-confirmation-v7/reader-closure.json`
- `docs/build-log/artifacts/horizonbench-confirmation-v7/result.json`

The result binds source, selection, construction evidence, authorization,
reader attempts, repository tree, runner, and tests by SHA-256. The six earlier
failed-closed arms remain registered as infrastructure evidence and were never
scored or reused.

## Verification

- Python: 838 passed, 8 skipped.
- Horizon focused contract: 26 passed.
- Evidence contract, computed power, spec drift, diff, and formatting: clean.
- Clippy all targets/features: clean with warnings denied; the ordinary
  incremental parallel invocation deadlocked in two sleeping Postgres clippy
  drivers, while the identical serial non-incremental invocation passed.
- Rust workspace all targets/features: passed; provider lints passed for plain
  Postgres, Supabase, and Neon.
- Scratch-Postgres ignored tests all passed, including 53 store contracts, 12
  mutation-ledger contracts, RLS/role/worker checks, and the hot-path SLO. The
  enclosing Cargo command was interrupted only after it entered an unrelated
  sleeping rustdoc process; the scratch database was dropped.
- The real binary/Postgres end-to-end probe passed all checks.
- Local `cargo test --doc` is unverified because `rustdoc` slept at 0% CPU on
  `memphant-core`; no doc-test success is claimed locally.
