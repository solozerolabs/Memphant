# Task 5 Fix Round 1: Admission Authority Closure

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

3. **Mechanical reader bound.** The census projects all 451 official questions
   into an oracle-free reader-shape JSONL containing only question ID, system
   prompt, question text, and image presence. The same Rust CLI tokenizes the
   exact official Qwen chat rendering with the pinned tokenizer and chat
   template. The derived maximum is 527 tokens, not the previous unexplained
   524 literal; 29 shapes contain an image marker. Tokenizer, template,
   question-source, fixture, row-count, and question-ID hashes are recorded and
   revalidated.

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

## Cost and claim boundary

The reader bound increases by 3 tokens, so `R` increases by 300 nano-USD. The
full 451-question two-arm total increases by 270,600 nano-USD and remains below
the frozen $200 ceiling. The hash-current census is the sole exact source for
the final total, headroom, hashes, and executable provenance.

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
