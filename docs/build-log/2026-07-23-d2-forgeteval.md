# D2 ForgetEval: packaged local reproduction

ForgetEval was acquired from the official `deeplethe/lethe` repository and
pinned at commit `b6053b7bdacc78a91b9ea4bb25f32edad278c495` under its MIT
license. The adapter imports the unmodified official case generator and runner.

MemPhant maps the supported protocol to current public REST verbs:

- `reset`: fresh server-bound subject/scope per case;
- `inscribe`: synchronous direct-unit `POST /v1/episodes`;
- `recall_texts`: Fast `POST /v1/recall`;
- `supersede`: rank-one recall followed by exact-unit `POST /v1/correct`;
- `release`: upstream adaptive-gap selection over immutable trace fused scores,
  followed by exact-unit `POST /v1/forget`;
- `purge`: N/A. Exact forget and subject erasure are not selective physical
  hard purge by natural-language identifier.

All runs used packaged release binaries, local embeddings, and run-owned
ephemeral scratch Postgres databases that were dropped after completion. Model
and paid calls were zero.

Results:

| suite | passed | failed | N/A | total |
|---|---:|---:|---:|---:|
| smoke | 12 | 0 | 3 | 15 |
| official template (`scale=200`, seed 42, four distractors) | 771 | 29 | 200 | 1,000 |
| current official adversarial | 133 | 126 | 126 | 385 |

The template failures were 27 amnesia and two drift cases. The adversarial
failures were 73 supersession, 30 amnesia, 19 decay, and four drift cases. They
are benchmark failures, not infrastructure errors. N/A is reported separately
and still remains outside the passed numerator.

Immutable reports:

- `artifacts/tri-sota-completion/forgeteval/smoke.json` —
  `03c04a1e4f8c60c4b36b3f257e32b11298722ec69d355f365c5fa5beafd2083b`
- `artifacts/tri-sota-completion/forgeteval/template-1000.json` —
  `eca2b8f857a91d6584330f50686c4d733194bb0421bd4abed963ab367e72fb3c`
- `artifacts/tri-sota-completion/forgeteval/adversarial-385.json` —
  `a88e06ec1636731a76137a3f9b3bcdab3e53c29aa6d3155b1a312d5e7d355788`

This closes D2 as executed negative/mixed evidence. It does not prove physical
purge, mark coverage, official leaderboard standing, production behavior, or
overall SOTA.
