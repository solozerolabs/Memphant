# D2 ForgetEval public-API proof

Date: 2026-07-24. Official upstream `deeplethe/lethe` is pinned at
`b6053b7bdacc78a91b9ea4bb25f32edad278c495` (MIT). Runs used packaged
MemPhant at clean tracked commit `ee4de593f1cd7bd2cef6fcca6d464d501c0c1da5`,
the current migration aggregate, local `small` embeddings, and a fresh scratch
Postgres database per suite. No paid provider was called.

| Suite | Passed | Failed | N/A | Total | Artifact SHA-256 |
|---|---:|---:|---:|---:|---|
| smoke | 12 | 0 | 3 | 15 | `5bff5558aea806b31a306cffdde97512598cc958a89e3c016f5df16679011c18` |
| template, scale 200 x 5 | 771 | 29 | 200 | 1,000 | `d9d88ed5705e36e400221228508e52f899c1e7036d51fce9bb42928321f6b681` |
| upstream adversarial | 133 | 126 | 126 | 385 | `100e573e52112076e78c7b4dd78fc32efa213778839bfac373e158c7598360ce` |

Template failures were 27 amnesia and 2 drift. Adversarial failures were 73
supersession, 30 amnesia, 19 decay, and 4 drift. Selective hard purge-by-query
has no public MemPhant primitive and is N/A; subject erasure was not substituted.
One upstream drift case is also N/A. These are not passes.

The adapter maps inscribe to a synchronous governed direct-unit retain, recall
to `/v1/recall`, supersede to rank-one recall plus exact-unit correct, and
release to an adaptive trace-gap selection plus exact-unit forget. Reports pin
the official harness hashes, exact argv, adapter, server/CLI binaries,
migrations, and clean repository identity.

This proves the D2 instrument is runnable and mutation semantics are measured.
It is a capability matrix, not a benchmark promotion or SOTA result. The high
adversarial failure count is a measured product gap, not an external block.
