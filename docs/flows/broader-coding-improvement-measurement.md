# Flow: broader outcome-coupled coding improvement measurement

## Spec

Run one fixed, paired 32-task measurement across autonomous one-shot,
interactive multi-turn, and short/delegated coding work. Each task starts from
an independent repository snapshot and compares no learned lesson (`C0`) with
one canonical validation reminder linked to an earlier validated source outcome
(`M1`). Prompts, lifecycle,
model, tools, validators, and requested end states are byte-identical within a
pair. Deterministic validators decide both co-primary outcomes; missing
linkage, privacy, model, settlement, restart, drain, or corpus evidence makes
the campaign non-decisional.

The 32 tasks use 32 distinct source repositories selected deterministically
from pinned SWE-bench Verified and Multilingual revisions. The primary model is
OpenRouter Claude Sonnet 5 through its documented direct Claude Code interface
and a body-free routing receipt recorder. Every Claude model role is pinned to
Sonnet 5; pair arms must use the same served upstream provider. The campaign has
a cumulative $80 liability ceiling and a strict one-attempt/no-retry rule. Private prompts,
transcripts, command streams, patches, and model bodies stay outside Git.
Public evidence contains only anonymous IDs, hashes, counts, finite outcome
fields, aggregate statistics, and gate predicates. A positive result supports
only the frozen model/runtime/task distribution; it is not general agent
improvement and does not change production policy automatically.

## Plan

1. Correct and commit the preregistration before source changes.
2. Add one campaign-specific module and focused tests, reusing the existing
   outcome/exposure endpoints, JSONL spool, scratch repos, and paired analysis.
3. Freeze and validate the 32 private task packets before paid dispatch.
4. Commit a contracted public-safe preregistration, run the campaign once,
   settle every attempt, drain the spool, scan privacy, and commit one result.
5. Run the full repository gate and report only the preregistered claim.

## Result — `BROADER_CODING_INCONCLUSIVE`

Ran once (settled cost $24.68 of the $80 ceiling). Both arms scored **0/32**
task passes: 0 wins, 0 losses, 0 discordant pairs, McNemar p = 1.0,
`decisional: false`. A floor effect — the pinned model/task tranche solved
nothing on either side, so C0-vs-M1 has no signal to compare. The result
neither confirms nor refutes the thesis; it says the floor must be lifted (an
easier or larger task tranche) before this axis can decide anything. Consistent
with the narrow finding that only the over-verification surface discriminates
and broad injection is flat/null.

The campaign runner, SWE executor, OpenRouter recorder, and their tests were
removed after the run — the recorded evidence (this doc plus the preregistration
and result artifacts under `docs/build-log/artifacts/outcome-coupled-evolution/`)
is retained; re-running would rebuild the runner against a lifted floor.

## Harness

```sh
python3 -m pytest tests/test_outcome_coupled_evolution.py -q
python3 scripts/check_evidence_contract.py
python3 scripts/instrument_power.py --check
python3 scripts/check_spec_drift.py
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
cargo test --doc
cargo run -p memphant-cli -- db lint --provider plain-postgres
cargo run -p memphant-cli -- db lint --provider supabase
cargo run -p memphant-cli -- db lint --provider neon
python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run
```
