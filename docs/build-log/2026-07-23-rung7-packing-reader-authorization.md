# Rung 7 packing reader gate: superseded and rejected

This historical request is **superseded, rejected, and not authorizable**. Its
machine packet is tombstoned as
`SUPERSEDED_REJECTED_BY_2026_07_24_FREE_EXACT_ABSTENTION_GATE` with
`authorization: null`. It provides no runnable command, model selection, call
cap, or spend ceiling.

The earlier free packaged rehearsal found a retrieval improvement at the same
8,192-token budget, but its exact-abstention subset regressed. The subsequent
frozen n=12 screen reproduced that binding failure: cap 1200 moved scored
hit@10 from 2/8 to 8/8 while exact abstention fell from 3/4 to 1/4. Zero paid
calls were made and settled cost remains $0.

The current decision and evidence live in:

- `docs/build-log/2026-07-24-packing-technique-screen.md`
- `docs/build-log/2026-07-24-next-evidence-authorization.md`
- `docs/build-log/artifacts/next-evidence/authorization-request.json`

The dormant paid packing runner, bootstrap, meter, campaign controller, and
analyzer were deleted. A materially different technique must pass both frozen
free utility and exact-abstention gates before a new paid campaign can be
designed and separately reviewed. The packing default remains off.
