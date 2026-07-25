# SWE-ContextBench and DeepSWE next evidence

Date: 2026-07-24. This report now includes the authorized economical Codex
first tranche and its validator-backed early-stop decision. It is not a
coding-memory efficacy, full-n=12, SOTA, deployment, or production claim.

## SWE-ContextBench result

The deterministic n=12 gate is frozen in
`benchmarks/manifests/swe_contextbench.kill.n12.json`. It spans nine Python
repositories and three arms per target: no memory, a same-repository unrelated
experience, and the official related experience retained through MemPhant. The
target prompt exposes only repository, base commit, instance ID, and problem
statement. Target patch, test patch, and official pass/fail fields are hidden
and represented only by hashes in proof.

All 12 official Lite evaluator image tags were resolved to registry digests
without pulling or running the images. The official dataset is MIT at revision
`5bec275a2095768a53ac804ae4fdf90b1723b8af`; its three required parquet files
are byte- and hash-bound in the manifest. The official code is pinned at
`31bb04155f52b184bf31b220e3cff0607ac9c953`; no license file was observed at
the audited revision and no upstream code was copied. The repository owner
explicitly authorized local benchmark use. Exact model, task-count, retry,
sandbox, timeout, and continuation bounds are frozen in
`docs/build-log/artifacts/next-evidence/coding/swe-contextbench-authorization.json`.

The packaged-REST rehearsal created, retained, recalled, and traced all 24
related and unrelated source experiences in a migrated scratch database. It
constructed the future outcome-mark payload but did not post a mark:

- 24/24 contexts created;
- 24/24 experiences retained and settled;
- 24/24 query recalls returned a trace and verified receipt;
- 24/24 future outcome-mark payloads were hash-bound;
- zero target solutions or hidden tests exposed;
- zero model calls, zero container runs, and $0 cost.

Artifact:
`docs/build-log/artifacts/next-evidence/coding/swe-contextbench-n12-rehearsal.json`
(SHA-256
`ac0d25221c176a6431816b3d52c560982045e98541b5ca6265fa9c7ebbf34cbc`).
The artifact also binds clean Git head/tree status, Cargo.lock, every migration,
the exact build command/toolchain, runner/runtime code, prompt contract, and
the three packaged binary hashes.
This proves adapter/runtime integrity only, not task resolution or coding-memory
benefit.

## Authorized first-tranche result

The minimum sequence ran four targets across all three frozen arms with
`gpt-5.6-sol`, medium reasoning, a 900-second per-task timeout, and zero
retries. All 12 calls completed in 2,727.527 seconds total. The eight memory
arms reconstructed the exact hash-bound recalled bodies and carried their
verified trace and receipt identities. No patch matched a target reference
hash or touched a test path.

Official Docker grading then resolved the first three no-memory baselines:

- Astropy 15082: 3/3 fail-to-pass and 132/132 pass-to-pass;
- Django 34176: 6/6 fail-to-pass and 166/166 pass-to-pass;
- Django 35356: 1/1 fail-to-pass and 17/17 pass-to-pass.

At that point the related arm could improve at most one of four targets, below
the preregistered requirement of at least two. Grading stopped immediately;
nine generated patches remain intentionally ungraded, and the remaining 24
model task runs were not executed. This rejects the current coding-memory gate
before broadening. It does not estimate a related-arm effect. Canonical result:
`docs/build-log/artifacts/next-evidence/coding/swe-contextbench-first-tranche-result.json`.

## DeepSWE pairing audit

The requested release was pinned exactly at
`e016041a6ccf8da29906afc9a3f5a8df940a1f78` (113 tasks, tree
`da6af978736343573c9f9560648c7a7f5e527a73`). Manual review required an
earlier upstream-ancestor base, a shared concrete subsystem or lifecycle, and
a reusable lesson that does not reveal the target solution. Same-repository or
same-base proximity alone was rejected.

Only three directed pairs survived:

1. Koota deferred mutation buffer to query predicates.
2. Koota deferred mutation buffer to pair-relation tracking.
3. Testem bail-on-failure reporting to per-launcher reports.

Three is below the frozen requirement of 12 unique targets. The paired memory
gate is rejected; arbitrary pairs will not be manufactured. DeepSWE may be
used only as a separately authorized unpaired robustness/outcome benchmark.
The exact task, verifier, ancestry, shared-file, and non-leakage locks are in
`benchmarks/manifests/deep_swe.pairing.audit.json` (SHA-256
`bb345a1981bb8f8fd450a9c29d16cb1f76ee9a62d4748aed06a81dc2805cb3f9`).
Each accepted pair includes the exact compare URL plus a reproducible canonical
hash of compare status, ahead/behind counts, total commits, and merge base. The
validator recomputes that projection and requires zero behind plus a merge base
equal to the prior base commit.

Focused verification after the execution runner and early-stop contract: 7
agent-gate tests, 6 SWE adapter tests, and 2 DeepSWE audit tests passed.
