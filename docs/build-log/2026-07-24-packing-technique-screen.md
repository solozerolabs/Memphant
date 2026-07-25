# Packing technique screen — 2026-07-24

## Boundary

This is a diagnostic, no-model LongMemEval-S screen. It selects the next
technique; it is not downstream answer-quality evidence and cannot promote the
packing default. The 12 cases were frozen before these runs: six known
cap-sensitive scored cases spanning all question types, four exact-abstention
sentinels, and two stable scored controls.

## Result

| arm | scored hit@10 | exact abstention | decision |
|---|---:|---:|---|
| current | 2/8 | 3/4 | control |
| render cap 1200 | **8/8** | 1/4 | reject: exact-abstention non-regression failed |
| utility / rendered token | 1/8 | 4/4 | reject |
| cap 1200 + utility / rendered token | 3/8 | 4/4 | reject |
| submodular order | 2/8 | 3/4 | no retrieval gain |
| cap 1200 + submodular order | **8/8** | 1/4 | reject: no ordering gain and exact-abstention non-regression failed |
| cap 1200 + local cross-rerank | **8/8** | 1/4 | reject: relevance reranking did not repair abstention |

The naive density method over-rewarded short distractors. Adding the render cap
did not repair it, so the session-quota mixture was not run: earlier complete
LongMemEval-S evidence had already found the quota ineffective or negative, and
another combination would not be an economical next test.

Artifacts (SHA-256):

- `lme-s-pilot-current.json`: `a925bef25d82000ecb55e6f58ff5d73850e1491a7e3afbcae0b16768fa4b65be`
- `lme-s-pilot-cap1200.json`: `da56ae3a5a3d5f0fa895728e0ebb1f2cf50082d688cbe2dece86a6f66f1f1442`
- `lme-s-pilot-utility.json`: `17e97f4d2073d8702e788a11c41d74de60c048cb47cb1bad6c7b3540a07c7ea4`
- `lme-s-pilot-cap1200-utility.json`: `235e6de51afac05009d63232f2d188ad20b85aa1ad601703234f8528cd1b6497`
- `lme-s-pilot-submodular.json`: `18ae18cdf824612ea71e316aad29a75fc31723f2d0160738b2d9ec1262601ed8`
- `lme-s-pilot-cap1200-submodular.json`: `472efe1a051fa416a3585988538852432f605a1bb4df835c86fe8aa78b992de3`
- `lme-s-pilot-cap1200-cross-rerank.json`: `196ec28d0b8f476500a8d2bebfb92e8826edf17dd23344f08ccc2ccf4f83b21d`

Every run used a new migrated scratch database, the same 12 source rows, local
`bge-small-en-v1.5`, `k=10`, pool 64, and an 8,192-token pack budget. Zero model
calls were made and cost was $0.

## Recent-method stop and research

Because both ordering attempts failed, experimentation stopped before any
larger run and recent 2026 work was reviewed:

- Bala, *What Survives Into Context* (arXiv:2607.00725, 2026-07-01) identifies
  answer-in-context as the packing diagnostic and uses cost-scaled greedy
  monotone submodular selection over relevance, query-term coverage, saturated
  representativeness, and source diversity. It also reports the important
  boundary that generic MMR underperformed and that the method helps only when
  complementary evidence is surfaced under a binding-but-not-extreme budget.
- Khurshid and Sehgal, *Structure and Diversity Aware Context Bubble
  Construction* (arXiv:2601.10681) independently recommends relevance anchors,
  marginal coverage, redundancy penalties, explicit diversity and an auditable
  deterministic selection trace.
- Fofadiya and Tiwari, *Adaptive Context Compression* (arXiv:2603.29193)
  supports importance, coherence, and dynamic budget allocation, but supplies
  less directly transferable deterministic selection detail.
- Recent practitioner reports likewise warn that raw similarity is not utility
  and that unequal or unstated context budgets invalidate memory comparisons;
  these are corroboration, not primary evidence.

The faithful deterministic submodular candidate was then run on the same n=12
bank. Relevance remains primary while marginal query coverage, saturated
lexical representativeness, and source diversity regularize each admission. It
preserved both stable controls and tied cap-only on aggregate retrieval and
abstention, but it did not improve either: several gold spans moved later in the
pack. It therefore has no retrieval claim and is rejected with the other tested
candidates.

## Decision-aware sufficiency-card rejection

The failed relevance mixtures motivated one materially different 2026 method:
judge whether evidence is sufficient and likely to change the decision, rather
than whether it is merely similar. [InfMem](https://arxiv.org/abs/2602.02704)
uses sufficiency-aware retrieval and stopping. [Decision-Aware Memory
Cards](https://arxiv.org/abs/2606.08151) scores necessity, expected outcome
uplift, and negative-transfer risk under an auditable schema; its released
[CICL implementation](https://github.com/stephen-guan-researcher/CICL) confirms
that hosted judges, local surrogates, and lightweight rankers share the same
typed boundary. [Learning What to Remember](https://arxiv.org/abs/2606.12945)
independently finds a multi-factor value model stronger than recency or any
single factor in its blind regime.

A hash-authorized n=12 screen adapted only that boundary. The controller saw
the question, date, rank, source session, and evidence body. Gold answers and
official answer-session IDs never entered its prompts. It had to choose the
smallest fully sufficient set, list missing evidence, and identify negative
transfer. Hard limits were 12 logical calls, 14 provider attempts, 512 output
tokens, and $1.25 maximum liability.

The kill gate fired after five calls, so the remaining seven were not spent.
Among the first four valid decisions, both abstentions were correctly rejected,
but only one of two answerable cases remained sufficient with an official
answer session. The 8/8 supported predicate was therefore already impossible.
The fifth response also put the same rank in `selected_ranks` and
`negative_transfer_ranks`; the local semantic parser rejected it even though
the provider's structural JSON schema could not express set disjointness. The
screen ended at **5 calls / $0.114075625 settled / $0 unsettled**.

The runner now persists a hash-only failure record and current settlement state
before re-raising any semantic parse error, preventing a partial result from
under-reporting a settled attempt. This post-run durability fix does not alter
or resume the rejected campaign.

Immutable closure artifacts:

- evidence input: `61f00323a444822e8d53f1c45dc77f1352b7991790b27b73c07fd4f86c9f71ff`
- partial decisions + failure: `4d86974ebbf4f2278fbd31f23cb912a956151d8bcaa64faf807c24694d65c04e`
- compiled partial evidence: `e2b35845d28a0cdf46bfabeeb61da179d842bcea6ea755a0d48c4eb8f89eaa13`
- paid attempt ledger: `ec4f92fd0f74bbac7ad983d8691249e779a7b774b8da2c243befcce0c5e9c737`
- closed authorization packet:
  `docs/build-log/artifacts/next-evidence/packing/sufficiency-authorization-request.json`

The technique is rejected; no downstream reader campaign, broader packing run,
default change, or packing promotion is justified.

## Rejected LongMemEval-V2 reader hypothesis

The exact n=12 follow-up is frozen in
`benchmarks/manifests/longmemeval_v2.packing-kill.n12.json`: six enterprise and
six web questions, eight answerable and four flawed-premise questions, with two
LLM-gotchas and four LLM-abstention judge cases. The source metadata validates
at pinned code commit `be15ea6e995462f3391c1a610892df3f67dfa7bd` and dataset
revision `f152293e235517d504809563c833d7190b8c713b`; all six questions in each
domain share one 100-trajectory haystack, so the paid gate needs only two free
constructions.

The discarded hypothesis paired no retrieval, current MemPhant, cap 1200,
cap 1200 plus submodular ordering, and an order-swapped negative control. No
reader or judge call was made.

The treatment adapter is sealed by
`benchmarks/manifests/longmemeval_v2_packing_adapter.lock.json`. It layers over
the historically locked packaged-REST adapter, verifies the selected server
arm from trace feature flags, emits compact unit/resource/trajectory receipt
provenance, records supported/contradicts-premise/near-match/insufficient
dispositions, and implements the negative control by reversing the exact same
selected context after recall. The shared server harness now closes inherited
packing environment variables and admits only explicitly selected values.

A real packaged dry run built the current server, CLI, and worker, migrated a
fresh scratch database, constructed one trajectory, drained the worker,
queried the cap-1200 server through REST, verified the trace flag and receipt,
emitted the companion packing proof, and dropped the database:

```text
MEMPHANT_LME_PACKAGED_INTEGRATION=1 ... pytest \
  tests/test_longmemeval_v2_packing_adapter.py::test_cap1200_packing_adapter_tiny_packaged_rest_dry_run -q
1 passed in 5.08s
```

This closes the free adapter/runtime rehearsal and rejects paid execution for
the tested techniques. The default remains off and the rung remains open. The
dormant paid runner, bootstrap, meter, and campaign analyzer were deleted. A
materially different technique must pass both frozen free gates before it can
justify a newly designed and separately reviewed paid campaign.
