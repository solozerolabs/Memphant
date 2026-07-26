# Task 1 report: campaign-wide attempt ledger and closure authority

Status: `DONE`

## Outcome

Implemented schema-v2 campaign authority in the existing append-only,
hash-chained, fsynced provider-attempt journal. The ledger now binds one
authorization scope and hard ceiling, carries opening liability, reserves each
cache miss before provider or credential access, accounts settled and
unresolved nano-USD liability across sequential screens, supports receipt-bound
reconciliation, and makes the terminal journal `closed` event authoritative
over its JSON projection.

No paid/model execution was authorized or performed.

## Files changed

- `scripts/provider_attempts.py`
- `scripts/run_reader.py`
- `scripts/generate_memora_memphant_answers.py`
- `scripts/generate_stale_memphant_answers.py`
- `scripts/run_packing_sufficiency_screen.py`
- `scripts/validate_memora_reasoning_proof.py`
- `scripts/run_memora_fama.py`
- `scripts/run_forgeteval_proposals.py`
- `scripts/run_restraint_bench.py`
- `scripts/run_stale.py`
- `benchmarks/memsyco/harness_bootstrap.py`
- `benchmarks/stale/harness_bootstrap.py`
- `tests/test_temporal_benchmark_contract.py`
- `tests/test_restraint_benchmark_contract.py`
- `tests/test_memora_benchmark_contract.py`
- `tests/test_run_reader_contract.py`
- `tests/test_generate_forgeteval_proposals.py`
- `.superpowers/sdd/2026-07-25-state-aware-resource-compiler/task-1-report.md`

## TDD evidence

Initial red run:

```text
python3 -m pytest tests/test_temporal_benchmark_contract.py tests/test_restraint_benchmark_contract.py -q
5 failed, 86 passed in 1.07s
```

The failures were the missing five-argument campaign constructor, cumulative
ceiling state, reconciliation/closure methods, and required SDK reservation.

Self-review found that untrusted meter context could overwrite
`max_liability_nanos`. The regression test failed first with `1 != 100000000`,
then passed after trusted fields were made authoritative.

## Verification

```text
python3 -m pytest tests/test_temporal_benchmark_contract.py tests/test_restraint_benchmark_contract.py tests/test_memora_benchmark_contract.py tests/test_run_reader_contract.py -q
192 passed in 1.36s

python3 -m pytest tests/ -q
859 passed, 12 skipped in 37.08s

python3 -m py_compile <all changed Python production files>
passed

git diff --check
passed
```

The first full-suite run exposed the hash-bound completed ForgetEval child
packet. Its generator was restored byte-for-byte; the exact provenance test
then passed, and the final full suite was green.

## Decisions

- Broke the old two-argument ledger constructor deliberately; no compatibility
  path can mint a journal without authorization scope, screen, hard ceiling,
  and opening liability.
- Used the exact campaign constants `200_000_000_000` hard-ceiling nano-USD and
  `4_258_002_400` opening-liability nano-USD in migrated production callers.
- Kept screen identity on journal events rather than authorization identity.
- Used `Decimal(str(value))` with `ROUND_CEILING` for provider cost settlement;
  float USD remains a non-authoritative compatibility summary only.
- Retained full reservations for starts, errors, interruptions, and unpriced
  results. A priced result substitutes rounded authoritative cost.
- Made the SDK client lazy: its real constructor is not invoked until after the
  journal has appended and fsynced the attempt reservation.
- Made OpenRouter credentials and cache directories lazy as well. `ReaderCli`
  checks authorization/open state before cache access; cache entries bind the
  authorization hash, exact cache key, attempt ID, start hash, and result hash.
- Appended and fsynced `closed` before attempting the atomic JSON closure
  projection, so projection failure cannot reopen a campaign.

## Review fix round

The independent review identified four critical and two important gaps. The
fix round closed each at the shared contract rather than adding per-run guards:

- **C1:** `open_campaign_ledger` now validates the new frozen campaign status,
  recomputes its authorization-scope hash, and opens only the packet's resolved
  canonical journal path. Every maintained paid screen uses this loader (or its
  environment-backed form) and varies only `screen_id`; caller-specific lock,
  manifest, and output hashes no longer mint campaign authorities.
- **C2:** the schema-v2 header binds the exact `10_000_000_000` nano-USD
  unallocated reserve. `assert_open()` still permits cache-only replay at the
  exact `200_000_000_000` ceiling, while every new start must fit at or below
  `190_000_000_000` total liability.
- **C3:** native SDK adapters no longer read OpenRouter credentials or construct
  generation-statistics lookups. The meter appends and fsyncs `start`, then
  invokes the credential-backed lookup factory and lazily constructs the SDK
  client. A regression test inspects the persisted `started` row from both
  boundaries.
- **C4:** caller metadata is stored only under `context` in start, result, and
  error payloads. It is never merged into provider evidence; collision tests
  cover `usage`, `response`, request/result hashes, requested/served model, and
  provider identity.
- **I1:** the header now binds a durable opening-reservation inventory whose
  exact sum equals opening liability. Reconciliation derives the reserved
  amount from a known unreconciled entry and requires its exact receipt and
  proof hashes before reducing carried liability.
- **I2:** the hash-pinned historical generator remains byte-identical at
  `faa1fbffcca57e3e62a98c4791a4daff15595b117d49d33e9f459670600e8356`.
  New execution uses maintained `scripts/run_forgeteval_proposals.py`, which
  validates the new campaign packet and opens its canonical journal. Current
  documentation labels the old path immutable historical evidence.

Fix-round RED evidence:

```text
canonical authority / reserve / exact-ceiling / context regressions: 4 failed
receipt-bound opening inventory regression: 1 failed
maintained ForgetEval entrypoint regression: 1 failed
```

Final fix-round verification:

```text
python3 -m pytest tests/test_temporal_benchmark_contract.py tests/test_restraint_benchmark_contract.py tests/test_run_reader_contract.py tests/test_generate_forgeteval_proposals.py -q
160 passed in 1.05s

python3 -m pytest tests/ -q
864 passed, 12 skipped in 27.11s

python3 -m py_compile <all changed Python production files>
passed

git diff --check
passed
```

No paid/model execution was authorized or performed during the fix round.

## Follow-up

- Legacy native scorer bootstraps now reserve a conservative
  `10_000_000_000` nano-USD per SDK attempt. Tasks 5-6 must replace legacy
  campaign entrypoints with the census-derived, authorization-bound exact
  reservation before any new paid execution. This task did not authorize such
  execution.
