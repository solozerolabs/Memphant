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

The naive density method over-rewarded short distractors. Adding the render cap
did not repair it, so the session-quota mixture was not run: earlier complete
LongMemEval-S evidence had already found the quota ineffective or negative, and
another combination would not be an economical next test.

Artifacts (SHA-256):

- `lme-s-pilot-current.json`: `a925bef25d82000ecb55e6f58ff5d73850e1491a7e3afbcae0b16768fa4b65be`
- `lme-s-pilot-cap1200.json`: `da56ae3a5a3d5f0fa895728e0ebb1f2cf50082d688cbe2dece86a6f66f1f1442`
- `lme-s-pilot-utility.json`: `17e97f4d2073d8702e788a11c41d74de60c048cb47cb1bad6c7b3540a07c7ea4`
- `lme-s-pilot-cap1200-utility.json`: `235e6de51afac05009d63232f2d188ad20b85aa1ad601703234f8528cd1b6497`

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

The next small candidate is therefore a faithful deterministic submodular
selection arm, not another scalar density score: relevance remains primary,
while marginal query coverage, saturated lexical representativeness, and
source diversity regularize each next admission. It must run on this same n=12
bank first. If it does not preserve the two stable controls and improve the
cap-only tradeoff, it will also be removed.
