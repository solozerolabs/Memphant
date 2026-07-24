# B4 verified receipts and calibrated answers — 2026-07-23

## Verdict

B4 is built and locally proven at the public contract boundary. Recall now
distinguishes an ordinary source reference from verified evidence. A verified
receipt is minted only after the selected unit is rejoined to its canonical
citation row and immutable episode/resource text and every binding below is
checked. Deep remains explicit and diagnostic-only.

This closes the B4 mechanism. It does not close packing reader QA, Deep
promotion, dogfood, launch, any benchmark rung, or an SOTA claim.

Machine summary:
`docs/build-log/artifacts/b4-verified-receipts/gate-summary.json`.

## Research and contract decision

Primary sources reviewed before freezing the schema:

- The current [official LongMemEval-V2 repository](https://github.com/xiaowu0162/LongMemEval-V2)
  and its Codex memory module at commit
  `6f020ac2fc3275e46c706d3406e02c3ed79b7be2`.
  The module uses `directly_supported`, `contradicts_premise`,
  `near_match_only`, and `insufficient` internally, with deterministic answer
  behavior. The public benchmark adapter itself requires returned context; it
  does not publish that quartet as its wire schema.
- [W3C Verifiable Credential Data Integrity](https://www.w3.org/TR/vc-data-integrity/)
  and [Sigstore bundle](https://docs.sigstore.dev/about/bundle/) designs. Their
  canonicalization and signature machinery solves portable
  authenticity across trust domains. B4 needs reproducibility inside the
  authenticated, tenant-bound service, so importing a generic signing/proof
  framework would add a second authority without improving the stated claim.

MemPhant therefore owns and versions the user-requested public vocabulary:

| Evidence status | Deterministic answer policy |
|---|---|
| `supported` | `answer_normally` |
| `contradicts-premise` | `state_premise_false` |
| `near-match` | `say_exact_target_not_found` |
| `insufficient` | `abstain_unknown` |

Unknown values, an answer-policy mismatch, or a blank reason fail closed.
Completed semantic claims require source IDs; capped and partial runs must be
`insufficient`. A semantic status cannot survive core recall unless at least
one returned citation has a verified receipt. An incomplete/insufficient Deep
run forces the top-level abstention signal.

These are MemPhant contracts, not a claim of LongMemEval wire compatibility.

## Receipt authority

No table, migration, or second evidence store was added. The implementation
reuses:

- `StoredCitation` for the citation ID, exact end-exclusive UTF-8 byte span,
  source ID, unit ID, context identity, and compile-time quote SHA-256;
- `StoredEpisode` / `StoredResource` for canonical source bytes, source
  identity, revision/content hash, trust, and ACL;
- `RetrievalTrace` for trace/query/policy/engine/time binding and persistence;
- the existing API-key/context-binding read path for tenant isolation.

`memphant.evidence_receipt.v1` binds the receipt contract, citation and trace
IDs, tenant/subject/scope/actor/agent/generation, unit and exact source ID,
source reference/revision/body SHA-256, span, quote SHA-256, source trust, query
hash, policy and engine revisions, database schema compatibility revision, and
recall time. SHA-256 makes the evidence reproducible; it is not a signature and
does not protect against a compromised database or operator.

An ID-only or derived reference remains explicit
`verification.status=unverified`; citation shape alone never means support.

## Fail-closed rules

Recall rejects a purported canonical receipt when any of these checks fails:

- selected unit, citation, and source do not share the exact tenant, subject,
  scope, agent, actor, and subject generation;
- the selected unit and citation disagree about episode/resource identity;
- the citation names zero or two sources, or canonical source text is missing;
- a source is quarantined; a resource has a non-empty ACL; or canonical source
  lookup is denied;
- a resource body no longer hashes to its stored content hash;
- the span is reversed, larger than 64 KiB, out of bounds, or not on UTF-8
  boundaries;
- the exact source slice does not hash to the stored quote hash;
- more than 64 verified receipts would be returned;
- a semantic Deep disposition has no verified receipt.

Ordinary resource ACL enforcement is still pending elsewhere in the product.
B4 therefore refuses to mint a resource receipt for every non-empty ACL instead
of treating dormant ACL metadata as authorization.

## Public contract changes

- `RecallCitation.verification` is required and is a closed tagged union:
  `verified { receipt }` or `unverified { reason }`.
- `EvidenceReceipt`, `EvidenceStatus`, `AnswerPolicy`, and
  `EvidenceDisposition` are generated into REST, MCP, and trace schemas.
- `DeepRecallSummary.evidence` is required.
- The OpenRouter `finish` tool now requires exactly `source_ids`,
  `evidence_status`, and a nonempty bounded `reason`.
- `openapi/memphant.v1.json`, `mcp/memphant.tools.v1.json`, and
  `examples/evals/trace-schema.v1.json` were regenerated by their owning
  binaries.

No schema migration was needed because receipts persist inside the existing
retrieval-trace JSON and are reproduced from immutable canonical source IDs.

## Focused proof

The focused gate recorded 91 passing tests, zero failures, zero ignored, 255
filtered by the deliberately narrow test selectors, and zero skipped:

- 2 evidence enum/policy/strict-deserialization tests;
- 3 core receipt tests for multibyte byte spans, deterministic JSON,
  quote-hash tampering, cross-tenant replay, protected ACL denial, and stale
  resource content;
- 18 Deep core tests, including semantic support without a verified receipt;
- 37 OpenRouter Deep tests, including strict finish status/reason handling;
- 1 resource retain/reflect/recall verified-receipt test;
- 24 REST/OpenAPI contract tests;
- 5 MCP generated-schema/resource tests;
- 1 trace-schema snapshot test.

The focused proof is not the repository-wide exit gate. The complete AGENTS.md
gate is run only after the program's final code change.

## Non-claims

- No paid/model calls were made; cost is exactly $0.
- No production data, secret, deployment, push, PR, merge, or remote CI was
  used.
- Receipt hashing is reproducibility, not cryptographic signer authenticity.
- B4 does not determine whether the cited bytes semantically support an answer;
  the evidence disposition is an assessment constrained by verified receipts
  and a deterministic policy.
- Deep is not promoted and no new Deep campaign is authorized.
- The full tri-SOTA continuation is not complete.
