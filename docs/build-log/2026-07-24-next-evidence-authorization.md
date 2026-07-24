# Tri-domain next-evidence authorization boundary

Date: 2026-07-24. Machine packet:
`docs/build-log/artifacts/next-evidence/authorization-request.json`.

The packet is deliberately non-authorizing: every executable campaign remains
`AWAITING_EXPLICIT_PAID_AUTHORIZATION`, with `authorization: null`. Zero model
calls were made and $0 settled.

## Minimum current request

| campaign | purpose | logical calls | provider attempts | ceiling | current state |
|---|---|---:|---:|---:|---|
| LongMemEval-V2 packing | 12 cases, five paired arms; reader plus judge | 90 | 90 | $8.00 | awaiting explicit authorization |
| ForgetEval proposals | 16 non-executing proposals over 12 cases | 16 | 32 | $0.50 | awaiting explicit authorization |
| SWE-ContextBench | four-target operational tranche, then conditional n=12 | 12 then at most 36 task runs | not frozen | not frozen | blocked, not authorizable |
| DeepSWE paired memory | required 12 causal targets | 0 | 0 | $0 | rejected at 3/12 pairs |

The current maximum authorizable dollar liability is therefore **$8.50**,
split across two independently scoped campaigns. Authorization for either does
not authorize the other.

## Packing controls

The packing packet binds all source, case, adapter, runner, bootstrap, meter,
acquisition, and slice-builder hashes. It fixes Qwen 3.5 9B as reader, GPT-5.2
as judge, the official prompts, 1,024 output tokens each, the same 8,192-token
context budget, ten exact arm/domain contexts, 90 total attempts, SDK retries
zero, parallelism four, and both provider-side and in-run limits at $8.00.
The meter journals and fsyncs before a request and reserves a conservative
UTF-8-byte-based maximum liability before provider access. Interrupted or
unsettled attempts retain their reserved liability.

Any provider, parser, receipt, trace, or evaluator error aborts the campaign.
There is no automatic retry or resume. Partial results are retained and marked
incomplete; a new run requires a new frozen packet. The two domain construction
passes share one ephemeral migrated scratch database and every treatment query
uses the same constructed state. The database is dropped at settlement.

The data acquisition command downloads only the five required source objects
(about 1.20 GB), not the approximately 6 GB trajectory screenshot archives.
Acquisition remains deferred until authorization because it has no value if the
paid gate is not approved.

## Forgetting controls

The forgetting child packet remains the executable authority and preserves its
$0.50 cap. It generates proposals only. Every proposal is emitted
`confirmed=false`; a later reviewer must approve exact body hashes in a new
confirmation ledger. Deterministic execution through the public exact-ID
mutation primitive requires separate authorization and is excluded here.

## Coding boundary

The SWE-ContextBench packet is not executable: the pinned official repository
had no observed license file, and the model/agent/compute ceiling has not been
frozen. DeepSWE is not eligible for a paired memory request because only three
causal pairs survived the 12-pair admission rule.
