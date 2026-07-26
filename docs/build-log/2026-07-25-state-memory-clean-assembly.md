# State-memory clean-chain assembly — 2026-07-25

## Result

The reviewed 65-commit state-memory chain was assembled on
`codex/memphant-state-memory-sota` from `origin/main`
`2dc93d1be2623a93e1eef7af57cc5f89be7064a6`, preserving commit history. The
assembled checkpoint is `5dda4f0f`.

Two semantic conflicts were resolved without compatibility paths:

- `STATUS.md` retains both the current-main P1-T6 reconciliation and the
  incoming B3 ledger.
- The benchmark merge retains the current-main trace-query assertion, UUID
  fixtures, and the single `jina-v5-small` registry entry.

The assembled chain exposed one immutable-campaign regression: the historical
LongMemEval-V2 adapter lock's OpenAPI hash had been re-pinned to current schema
bytes. Commit `5dda4f0f` restores the original campaign digest
`a5bac765d7c4c862a342d95b49049c27d3af57aea9f80af6d3a0a489ac055271`.

## Verification at `5dda4f0f`

- `python3 -m pytest tests/ -q`: 849 passed, 12 skipped.
- `cargo fmt --check`: passed.
- `cargo clippy --all-targets --all-features -- -D warnings`: passed.
- `cargo test --all-targets --all-features`: passed.
- `cargo test --doc`: passed.
- Scratch-Postgres ignored store/worker suite: 75 passed, 0 failed.
- Provider lint: clean for plain Postgres, Supabase, and Neon.
- Migration dry-run: three ordered migrations.
- Real binaries + scratch Postgres `scripts/e2e_probe.sh`: all checks passed.

The explicit private mirror check remains unmet and was not hidden:

```text
spec_drift=dirty
08-api-sdk-mcp-spec.md:content
STATUS.md:content
```

The private checkout predates multiple already-landed public API and ledger
changes. It was left untouched, as required. This is a mirror-parity failure,
not a code/test failure and not evidence that the assembled chain is fully
green.
