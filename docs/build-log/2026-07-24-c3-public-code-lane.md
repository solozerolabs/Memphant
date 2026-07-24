# C3 public coding-continuity volume proof

Date: 2026-07-24. Classification: public synthetic agent trajectories, not
organic production traffic. No model or paid provider was called.

## Immutable inputs

- Dataset: `nebius/SWE-rebench-openhands-trajectories`, CC-BY-4.0.
- Dataset revision: `35455389ab51bf5e2306bfd436ef72d0f98bf882`.
- Corpus: 495 whole attempts, 64,550 source messages, 64,055 emitted events.
- Corpus SHA-256: `c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669`.
- Golden SHA-256: `6a5f6978c922abe41be0a1ebc232248f7baa0dd4c897212ffe2879655681ed97`.
- Runtime commit: `9c37fff3840e4ede504d0f67b09acb9c3613cb32`, tracked tree clean.
- Machine proof: `docs/build-log/artifacts/c3-public-code-lane-v2/provenance-final.json`
  (SHA-256 `f8ba2c445772000701b800f7fe3e1380990446d2c09b71d0a6df26f0e6b1c8fc`).

The transform omitted 495 system messages with accounting, omitted zero empty
messages, and clipped 4,811 events at 4,000 characters (36,405,709 characters /
36,480,400 bytes removed). Every unknown role fails closed. Gold answers remain
immutable grader data; retrieval uses a separately sealed, source-derived prior
action and rejects missing queries or answer leakage.

## Packaged scratch-Postgres run

- 495 independent attempt contexts under tenant A; one identical-binding and
  identical-source-ref isolation sentinel under tenant B.
- 64,056 raw writes and 64,056 completed worker jobs.
- 64,014 episodic projections; 42 repeated bodies were exact in-context dedup,
  accounting for every raw episode. Zero dead or pending jobs.
- Ingest: 317.515 s (201.739 events/s). Compile: 641.597 s. Recall: 2.808 s.
- Two-tenant owner-to-sentinel and sentinel-to-owner negative checks passed.
- Adversarial continuity: Recall@5 = 12/40 (0.300), Recall@10 = 18/40
  (0.450). All 40 queries returned ten items.

This closes the realistic-volume, provenance, isolation, source-accounting,
bounded no-model runner, and deterministic adversarial-retrieval predicates.
It does not prove reader answer quality, outcome-marked memory, validator-backed
held-out task success, production traffic, live Syndai coding ingestion, or
SOTA. The paid reader remains awaiting the separately frozen authorization
packet.

## Root fixes exposed by the run

The first sealed attempts correctly failed and were retained as diagnostic
evidence. The final path separates retrieval and grader queries; prevents an
invalid implicit Deep/no-embedding configuration; keeps low-trust raw
episodic/resource evidence recallable without promoting semantic claims;
preserves short raw evidence outside the inferred-claim noise floor; and makes
the runner reconcile raw writes, exact dedup, projections, done/dead/pending
jobs before retrieval.
