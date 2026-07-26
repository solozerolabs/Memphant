# Task 5 Fix Round 1: Admission Authority Closure

> Superseded cost evidence: round 2 replaced the text-only 527-token reader
> proxy with exact multimodal row inventory and provider-ceiling liability. See
> `task-5-fix-round-2-report.md` and the canonical `CAMPAIGN-CENSUS.json` for the
> current authorization. The implementation and test evidence below records the
> round-1 state and is not an active cost packet.

## Status

All three P1 review findings are implemented test-first. No paid/model calls,
credentials, production systems, or databases were used. Historical P1-T6
evidence remains byte-identical, and the progress ledger was not modified.

## P1 closures

1. **Pre-launch admission authority.** The credential-bearing construction API
   now accepts census and manifest paths, reloads both packets, validates their
   hashes and current production identities, independently derives `C`, `R`,
   `S`, retry headroom, and the full admission equation, and rejects any drift
   before the campaign ledger or launcher is touched. The lower reservation
   helper accepts only the already-validated in-process object.

2. **Exact census executable provenance.** Census no longer chooses an existing
   debug/release binary. It performs a fresh isolated
   `cargo build --locked --release -p memphant-cli`, binding Cargo.lock, the
   current source set, cargo/rustc identities, profile, package, and resulting
   binary SHA-256. It then executes a private content-addressed copy and checks
   the copy hash both immediately before and after execution. Pre-launch
   validation repeats the fresh build and requires identical provenance.

3. **Mechanical reader bound (superseded in round 2).** This round projected
   image presence but did not process the exact image bytes. Round 2 now binds
   all 29 official screenshots and reserves the provider prompt ceiling for
   those rows; the 527-token maximum remains valid only for the 422 text-only
   local processor diagnostics.

## TDD evidence

Observed RED failures covered:

- a syntactically valid, freshly self-hashed over-cap census reaching the old
  dictionary-based launch API;
- absent stale-binary/provenance validation;
- absent mechanical reader proof validation;
- absent exact Qwen reader renderer.

GREEN regressions prove:

- the forged over-cap census records no ledger event and never calls launch;
- a deliberately different executable is rejected against the fresh locked
  build SHA;
- tokenizer, template, question fixture, or question-shape drift invalidates
  reader authority;
- oracle answer/evaluator fields never enter the reader fixture;
- the exact system/user/image/generation-prompt rendering remains pinned.

## Cost and claim boundary (historical, superseded)

The round-1 cost paragraph below is retained only as review history. It is not
current authority. Round 2's hash-current canonical census is the sole exact
source for the final total, headroom, hashes, and executable provenance.

The final census records `C = 65,220,602,500`, `R = 125,945,700`, and
`S = 14,310,400` nano-USD. Including the prepaid retry pool, the admitted total
is `$199.5356167`, leaving `$0.4643833` below the hard ceiling. Its self-hash is
`66deb70b49f75099f100511617d581524d823841c47aefded10537506ba4cec6`,
its file hash is
`25346b0afc89d9a2e9d3cdaa76ad8bfa3325fb6b4c399722d03719dfd72d7ce1`,
and it binds manifest hash
`195881dbf17e6192eb47b64a76c65b7784ad390bf182df606041e2cd4544026e`
and freshly built census executable hash
`a0ff3eace7c44538090cceba20e6c24828042d17dbce1156be413ec3c5135031`.
The independent pre-launch validator repeated the fresh locked build and
accepted that exact packet and binary provenance.

## Verification

- `python3 -m pytest tests/ -q`: `891 passed, 12 skipped`.
- `cargo fmt --check`, `cargo clippy --all-targets --all-features -- -D warnings`,
  `cargo test --all-targets --all-features`, and `cargo test --doc` passed.
- Provider lint passed for `plain-postgres`, `supabase`, and `neon`.
- Migration dry-run passed. Spec drift was skipped cleanly because the private
  Syndai spec checkout was unavailable.
- Scratch-Postgres ignored tests and the binary E2E probe could not run because
  the required `memphant-postgres-1` container was not running.
- Historical P1-T6 runner and manifest hashes remained unchanged.

This remains a no-model admission proof. It is not evidence that the paid
campaign ran, that any benchmark predicate passed, or that MemPhant achieved
SOTA.
