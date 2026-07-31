# MemPhant Release Process

MemPhant public releases are source tags from the public repository. Hosted
deployments run the same released binary and may add only private control-plane
state for billing, SSO, region routing, and metering.

## Required Gates

Run these before tagging a release candidate:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
cargo test --doc
python -m pytest tests -q
python3 scripts/instrument_power.py --check
python3 scripts/check_evidence_contract.py
cargo run -p memphant-eval -- verify-golden examples/evals/golden.yaml
cargo run -p memphant-eval -- run benchmarks/nightly-sampled.yaml --archive-traces --archive-dir docs/build-log/artifacts
cargo run -p memphant-eval -- security examples/evals/security-smoke.yaml
cargo run -p memphant-cli -- db bootstrap-check --provider plain-postgres
cargo run -p memphant-cli -- db bootstrap-check --provider supabase
cargo run -p memphant-cli -- db bootstrap-check --provider neon
cd web && npm test
```

For local cross-repo dogfood work, also run:

```bash
python3 scripts/check_spec_drift.py
```

That drift gate is intentionally local because the public CI runner does not
have the private Syndai worktree.

## Release Checklist

1. Confirm `docs/launch/public-launch-scorecard.json` points to a current
   public benchmark profile with archived traces, harness configuration,
   p95 latency, cost, security, and deletion results.
2. Confirm `SECURITY.md`, `CONTRIBUTING.md`, self-host docs, API/MCP schemas,
   CLI help, Python SDK examples, and the web launch surface are present.
3. Run the provider bootstrap checks for plain Postgres, Supabase, and Neon.
   Supabase profile checks must keep `memphant` out of exposed PostgREST
   schemas and require advisory/lint review with warning-level failure.
4. Confirm no public API, SDK, MCP, CLI, or web surface contains hidden
   Syndai-only fields or behavior.
5. Confirm the release notes do not claim SOTA unless the scorecard names the
   exact winning axis, baseline, benchmark version, trace archive, cost,
   latency, and security/deletion result.
6. Tag as `vMAJOR.MINOR.PATCH` only after the above gates pass on the release
   candidate commit.

## Claim Policy

No public SOTA claim is made by default. A claim can be added only when the
scorecard records a reproduced public benchmark win and names the exact axis.
Vendor-reported or internal-only numbers may be shown as context, but cannot
anchor a SOTA claim.
