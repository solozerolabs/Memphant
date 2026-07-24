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
| render cap 1200 | **8/8** | 1/4 | retain as retrieval candidate; reader and abstention gates still open |
| utility / rendered token | 1/8 | 4/4 | reject |
| cap 1200 + utility / rendered token | 3/8 | 4/4 | reject |
| submodular order | 2/8 | 3/4 | no retrieval gain |
| cap 1200 + submodular order | **8/8** | 1/4 | tied with cap on retrieval; retain only as the downstream ordering arm |

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
pack. It therefore has no retrieval claim and cannot replace cap-only. It is
retained solely as the required, mechanistically distinct ordering arm for the
paired reader test; a downstream null or regression removes it.

## LongMemEval-V2 frozen reader gate

The exact n=12 follow-up is frozen in
`benchmarks/manifests/longmemeval_v2.packing-kill.n12.json`: six enterprise and
six web questions, eight answerable and four flawed-premise questions, with two
LLM-gotchas and four LLM-abstention judge cases. The source metadata validates
at pinned code commit `be15ea6e995462f3391c1a610892df3f67dfa7bd` and dataset
revision `f152293e235517d504809563c833d7190b8c713b`; all six questions in each
domain share one 100-trajectory haystack, so the paid gate needs only two free
constructions.

The paired arms are no retrieval, current MemPhant, cap 1200, cap 1200 plus
submodular ordering, and an exact order-swapped copy of the latter. Every arm
uses the official `Qwen/Qwen3.5-9B` reader, official `gpt-5.2` judge, official
generation/scoring code, and the same 8,192-token recall/reader budget. This is
60 reader calls plus 30 judge calls before SDK retry liability. No call has
been made.

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

This closes the free adapter/runtime rehearsal only. The default remains off,
the rung remains open, and downstream answer quality still awaits explicit
paid/model authorization.
