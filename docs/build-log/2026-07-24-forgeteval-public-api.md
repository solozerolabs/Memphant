# D2 ForgetEval public-API proof

Date: 2026-07-24. Official upstream `deeplethe/lethe` is pinned at
`b6053b7bdacc78a91b9ea4bb25f32edad278c495` (MIT). Runs used packaged
MemPhant at clean tracked commit `aae4b97aea1a5e8dd0377a8a3566da2bc6be225a`,
the current migration aggregate, local `small` embeddings, and a fresh scratch
Postgres database per suite. No paid provider was called.

| Suite | Passed | Failed | N/A | Total | Artifact SHA-256 |
|---|---:|---:|---:|---:|---|
| smoke | 12 | 0 | 3 | 15 | `097f2ed438f27a8f0273faf09e8929b99da435ea4c277c38e9c46c39fca3dc45` |
| template, scale 200 x 5 | 771 | 29 | 200 | 1,000 | `2af663ccd2a5f0e63a9d91ce7b3fd81e5bea523f1353f21ee4c6743bb9384851` |
| upstream adversarial | 133 | 126 | 126 | 385 | `30cc1fc676a411b248f62fa45ab76b1c0e033d51e5f60b5693096de3da72130b` |

Template failures were 27 amnesia and 2 drift. Adversarial failures were 73
supersession, 30 amnesia, 19 decay, and 4 drift. Selective hard purge-by-query
has no public MemPhant primitive and is N/A; subject erasure was not substituted.
One upstream drift case is also N/A. These are not passes.

The adapter maps inscribe to a synchronous governed direct-unit retain, recall
to `/v1/recall`, supersede to rank-one recall plus exact-unit correct, and
release to an adaptive trace-gap selection plus exact-unit forget. Reports pin
the official harness hashes, normalized portable argv, adapter, server/CLI
binaries, migrations, and clean repository identity without committing
machine-local absolute paths.

This proves the D2 instrument is runnable and mutation semantics are measured.
It is a capability matrix, not a benchmark promotion or SOTA result. The high
adversarial failure count is a measured product gap, not an external block.
