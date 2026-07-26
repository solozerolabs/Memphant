# Task 5 Fix Round 2: Multimodal Reader Liability Closure

## Status

The P1 multimodal census finding is closed test-first. No paid/model calls,
credentials, production systems, or databases were used. Historical P1-T6
runner and manifest hashes remain byte-identical, and the progress ledger was
not modified.

## Root-cause closure

Round 1 used a text rendering plus one image sentinel, so its 527-token maximum
did not establish the exact 29 official image-bearing Qwen messages or a valid
OpenRouter billing ceiling. Round 2 replaces that proxy with two separately
scoped authorities:

- A pinned no-model Qwen processor sidecar validates the exact 451 official
  messages. It binds all 29 upstream screenshot paths, bytes, SHA-256 values,
  PNG dimensions, data-URL construction, processor sources, package versions,
  Python/uv toolchain, tokenizer, chat template, and every per-row result.
- The local processor result is diagnostic because OpenRouter bills native
  provider-reported tokens. The cost authority therefore uses exact local input
  tokens plus 200,000 output tokens for each of 422 text-only rows, and the full
  documented 262,144-token provider prompt ceiling plus 200,000 output tokens
  for each of the 29 image rows.

The exact row inventory is reconstructed and hash-checked by the census,
independent prelaunch validation, and construction-wave ledger context. Missing,
tampered, wrong-dimension, or differently inventoried screenshots fail before
any ledger reservation or credential-bearing launch.

## Exact evidence

- Rows: 451 total; 422 text-only; 29 image-bearing.
- Text-only local tokens: sum 86,119; minimum 152; maximum 527.
- Image local diagnostic tokens: sum 31,439; minimum 1,052; maximum 1,188.
  The maximum is question `914ab1d4`, a hash-bound 1280x720 PNG with processor
  grid `[1,44,80]`.
- Row-token inventory SHA-256:
  `6acb3cc71e3358abacabc481608eb4d0620430d1fe108790e79cc6d2952260b5`.
- Image inventory SHA-256:
  `02179d546fa700ca6c097e1ac84f5331c45b735429dd7efcf4621002c81d3fb0`.
- Reader liability inventory SHA-256:
  `27daa558bf86e956dc2865649e59560619300687a97009e7f874e868ff69f022`.
- `R_sum = 56,966,572,500` nano-USD.

The exact admission formula is
`4258002400+C+2*R_sum+451*S+10000000000<=200000000000`, with
`C = 65,220,602,500` and `S = 14,310,400` nano-USD. The authorized total is
`199,865,740,300` nano-USD (`$199.8657403`), leaving `134,259,700` nano-USD
(`$0.1342597`) beneath the hard ceiling while preserving the independent $10
contingency. `paid_models_run = false` and `spend_nanos = 0`.

The sole current authorization artifact is the canonical
`docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/CAMPAIGN-CENSUS.json`.
Its self-hash is
`a1437881dd8c8ed2fc33a26d213231a0dd0790532000c759ae6486617313e0a2`, its
file hash is
`64fd28bf20a7b33d7d7d35977ab4a3ad26cbf61c60047658565c46726cac8c3d`, it
binds manifest hash
`ae0c238f1793b33bd63e73645e4a751926969c0186ae7b5ee477639220a68970`,
and its fresh locked census binary SHA-256 is
`5ea144fd8a4fa65585097d592638cdf5041ed20831f5553bd12ec2f01c3d5973`.
The explicitly named baseline and Luna-Deep census files remain rejected
historical feasibility snapshots and are not current authorization packets.

## TDD and verification

RED failures covered the old formula, absent row liability inventory, absent
pinned processor proof, fixture omission of screenshot identity, acceptance of
missing/tampered screenshots, and absent dimension inventory.

GREEN evidence:

- Focused Python: `29 passed`.
- Full Python: `893 passed, 12 skipped`.
- Independent prelaunch validation accepted the exact packet after repeating
  screenshot, processor, manifest, and fresh locked binary validation.
- `cargo fmt --check`, `cargo clippy --all-targets --all-features -- -D warnings`,
  `cargo test --all-targets --all-features`, and `cargo test --doc` passed.
- Provider lint passed for `plain-postgres`, `supabase`, and `neon`.
- Migration dry-run passed. Spec drift skipped cleanly because the private
  Syndai spec checkout was unavailable.
- Scratch-Postgres and binary E2E probes were not run because the required
  `memphant-postgres-1` container was not running.
- Historical P1-T6 runner SHA-256 remains
  `e1321f09a1ba70b58bcf9a9e887da65053434e08cbda56f4a6a84aa8e2013b88`;
  its manifest remains
  `09d149423ad0ec1591f34a07bcc46b106a5c2111a043c6c1d8bb384c254b74c2`.

This is a no-model admission proof only. It is not evidence that the paid
campaign ran, that any benchmark predicate passed, or that MemPhant achieved
SOTA.
