# Task 6 case-bank proof repair

Date: 2026-07-26

Status: ready for independent rereview. This is a no-model proof repair; no
authorization packet was present, no provider credential was read, and no paid
request was made.

## Review findings closed

- The synthetic smoke now builds its construction binding with the campaign's
  canonical builder, validates it with `load_canonical_binding`, and derives
  cache and ledger receipts with `derive_construction_receipts`. The proof and
  case-bank manifest cross-check the exact authorization, census, selection,
  compiler, provider, cache, ledger, plan, and materialization identities.
- A recomputed proof whose self-consistent binding is unrelated to the canonical
  binding is rejected by regression coverage.
- Public proof artifacts contain stable repo-relative binary labels and redacted
  Postgres client labels. The smoke and regression suite reject workstation,
  temporary-directory, worktree, and repeated-character placeholder hashes.
- Python receipt validation uses one finite-only Serde-compatible numeric JSON
  encoder. Matching Python and Rust fixtures cover `1.75e-6`, `1e-7`, `-1e-7`,
  `1e+20`, and `-0.0`; exponent normalization does not rewrite JSON strings.

## Final scratch proof

Command:

```sh
python3 scripts/smoke_lme_v2_case_bank.py \
  --artifact-root docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/scratch-case-bank-smoke
```

Result: PASS.

- canonical binding validated: `true`
- canonical receipts validated: `true`
- binding SHA-256: `2c6f6411b7e1895a7af8041d0987c053229bd5325b3362b4938d1a973a226432`
- construction proof SHA-256: `b1b2d357d5d1ad9415dad0ec086ab6e7c6ed1e897e01f23bcb21f7a2929bfc45`
- runtime case-bank SHA-256: `ab05fe6def734fa7d0943c968adb46e1ee9cfa2c68f038cd6701dc3f543848b5`
- cache-hit receipts: `1`
- Fast clone trace: `true`
- Deep clone trace: `true`
- provider credentials read: `false`
- Postgres toolchain SHA-256: `4be04b1cad42123260fe4f7cc79345055883d08d2750dfb4fbb4aa2dc786eff5`

The committed `CASE-BANK-PROJECTION.json` and
`CONSTRUCTION-BINDING-PROJECTION.json` are public projections, not replacement
canonical authorities. The runtime smoke validates the full temporary
authorities before publishing projections that omit machine-local paths.

## Checks

- `python3 -m pytest tests/test_run_lme_v2_state_aware.py -q` — 55 passed.
- `python3 -m pytest tests/test_public_benchmark_adapters.py -q` — 22 passed,
  1 skipped.
- `cargo fmt --check` — passed.
- `cargo clippy -p memphant-runtime --all-targets --all-features -- -D warnings`
  — passed.
- `cargo test -p memphant-runtime serde_json_number_encoding_matches_campaign_python_contract`
  — passed.
- `python3 scripts/check_spec_drift.py` — skipped because the private mirrored
  spec checkout was absent at the expected sibling path.
- Adapter-lock and construction-manifest source hashes were recomputed and
  matched all four changed source files.
- Public artifact scan found no local workstation/temp/worktree path or
  repeated-character placeholder SHA-256.

No full LongMemEval-V2 census, construction wave, reader, judge, or Deep recall
run was authorized or executed by this repair.
