# MemPhant Agent Instructions

MemPhant is the public Apache-2.0 memory substrate repo. Treat `docs/superpowers/specs/memphant/STATUS.md` as the live ledger and flip checkboxes only with the proof artifact named in the same change.

## Repo Boundaries

- Public product work lives in this repo: Rust crates, migrations, the Python SDK, public docs, public fixtures, provider lint, and the self-hostable runtime.
- Private Syndai integration and porting boundaries are described in `porting.md`; do not track a local Syndai worktree path in this repo.
- Keep mirrored MemPhant spec files drift-free when a private Syndai checkout is available.
- Never commit secrets. Use `.env.example` for local variable names only.

## Sister Project and Secrets

- Syndai is MemPhant's private sister project. Until MemPhant has a separate Doppler project, the `syndai` Doppler project is the canonical secret source for MemPhant private integration and explicitly authorized live or paid benchmark work.
- For local development and benchmark work, wrap only the secret-consuming command with `doppler run --project syndai --config dev -- ...`; use `--config prod` only when the task explicitly targets production. Always pass the project and config because linked worktrees do not inherit Syndai's directory binding.
- CI, unit and integration tests, provider lint, no-model verification, and ordinary local development must remain secret-free and must not be wrapped in Doppler.
- Never print, download, copy, or persist Doppler values into this repo, `.env` files, logs, artifacts, shell output, or commits.
- Shared Doppler does not imply shared database authority: MemPhant tests and benchmarks must continue using local or ephemeral scratch Postgres and must never target a Syndai production database unless the user explicitly authorizes that exact operation.

## Database Rules

- MemPhant-owned database objects must live in the `memphant` schema.
- Do not create or modify application objects in `public`.
- Tenant identity is derived server-side from API keys; every tenant-scoped read/write is tenant-bound (traces included).
- Keep provider lint green for `plain-postgres`, `supabase`, and `neon`.

## Working Rules

- Use current docs when touching libraries, providers, CLIs, or cloud services; prefer Context7 plus official web docs.
- Fix root causes and add tests or contract checks for regressions.
- Do not add compatibility shims, feature-flag rot, or temporary bypass paths in this pre-production repo.
- Preserve unrelated dirty work in this repo and in any private Syndai checkout.
- `openapi/memphant.v1.json` and `mcp/memphant.tools.v1.json` are generated artifacts — regenerate via the server/mcp binaries, never hand-edit.

## Benchmark Dataset Cache

- Benchmark corpora and paid-run caches live OUTSIDE the repo under two roots: `~/.cache/memphant-bench/` and `~/.cache/memphant/`. Both hold real benchmark state, not disposable build output — do NOT include either in disk cleanups or blanket `rm -rf ~/.cache` sweeps. `~/.cache/memphant-byo-minilm/` holds a pinned local embedder.
- `~/.cache/memphant-bench/` holds the public corpora: `longmemeval-v2-<rev>/` (~13 GB, most of it `data/trajectory_screenshots/`, from HuggingFace `xiaowu0162/longmemeval-v2`), `memora/` (~1.1 GB), `sota-paid-rung/` (~307 MB), plus per-run state/stale dirs and the flat sha256-named downloads owned by `scripts/ingest_public_bench.py`. Refetching LongMemEval is a ~13 GB network pull — costly, not free.
- `~/.cache/memphant/` is the LongMemEval-V2 data root used by `scripts/run_lme_v2_state_aware.py` (`--data-root` / `MEMPHANT_LME_V2_DATA_ROOT`). It holds campaign state that is **not** re-downloadable, so it is the more dangerous of the two to delete.
- `--cache-dir` means two different things: a *corpus* cache in `run_memora_fama.py` / `run_stale.py` / `run_state_bench.py`, and a *paid provider-response* cache in `run_reader.py`, `generate_*_answers.py`, `code_lane_mine.py`, and `gate_mine_goldens.py`. Deleting the latter re-spends real money.
- Pinned dataset revisions are part of promotion provenance (`benchmarks/manifests/*.json`, `STATUS.md`): deleting a cached revision can invalidate reproduction of a recorded run.
- Paid-run response bodies and resumable state under `docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot*/` are gitignored by design and live only on disk. Only the compact authorization/census/canary-gate proofs are committed. Each pilot directory carries a local (also gitignored) `SHA256SUMS`; verify a copy with `shasum -a 256 -c SHA256SUMS` from inside that directory, and regenerate it after any authorized resume writes new bodies.
- `benchmarks/` is a namespace package and the repo has no `pyproject.toml`, so scripts importing it must be invoked with the repo root on the path: `PYTHONPATH=. python3 scripts/<script>.py ...`. `scripts/run_lme_v2_state_aware.py` is pinned by sha256 in the v5 campaign manifest — do not "fix" its imports; that would invalidate a live authorization chain.

## Verification

Run the narrowest meaningful checks while iterating, then the full gate before claiming a workstream exit:

```sh
python3 -m pytest tests/ -q
python3 scripts/check_spec_drift.py
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets --all-features
cargo test --doc
# Live-Postgres contract + worker-binary smoke tests: #[ignore]d by default.
# with_scratch_db.sh mints an ephemeral migrated database, points
# MEMPHANT_TEST_DATABASE_URL at it, and drops it afterward — so these tests
# never leave job_state/tenant debris in the shared campaign DB (the recurring
# worker-starvation incident). The base URL is only used to reach the server
# and create the scratch DB; the tests never touch `memphant` itself.
bash scripts/with_scratch_db.sh postgres://memphant:memphant@localhost:5432/memphant \
  MEMPHANT_TEST_DATABASE_URL \
  cargo test -p memphant-store-postgres -p memphant-worker -- --ignored --test-threads=1
cargo run -p memphant-cli -- db lint --provider plain-postgres
cargo run -p memphant-cli -- db lint --provider supabase
cargo run -p memphant-cli -- db lint --provider neon
python3 scripts/apply_memphant_migrations.py --database-url postgres://memphant.invalid/memphant --dry-run
# Real binaries + real Postgres end-to-end probe; requires a running
# memphant-postgres-1 container (compose service `memphant-postgres`) on :5432.
# The probe self-provisions an ephemeral scratch DB from this base URL and
# drops it — it never touches the shared `memphant` DB, so foreign job debris
# cannot starve it.
DATABASE_URL=postgres://memphant:memphant@localhost:5432/memphant bash scripts/e2e_probe.sh
```

## CI monitoring

After pushing to remote main, verify CI is green before claiming done. Poll no more
often than **once every 2 minutes** (`gh run list --branch main --limit 1` /
`gh run watch`) — CI runs take minutes; tighter polling wastes quota and adds noise.
