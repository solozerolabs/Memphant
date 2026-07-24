# Tri-domain next-evidence authorization boundary

Date: 2026-07-24. Machine packet:
`docs/build-log/artifacts/next-evidence/authorization-request.json`.

The packet is deliberately non-authorizing. Only the ForgetEval proposal
campaign remains `AWAITING_EXPLICIT_PAID_AUTHORIZATION`, with
`authorization: null`. Packing is rejected before paid execution. Zero model
calls were made and $0 settled.

## Minimum current request

| campaign | purpose | logical calls | provider attempts | ceiling | current state |
|---|---|---:|---:|---:|---|
| LongMemEval-V2 packing | 12 cases, five paired arms; reader plus judge | 0 | 0 | $0 | rejected: free exact abstention regressed 3/4 to 1/4 |
| ForgetEval proposals | 16 non-executing proposals over 12 cases | 16 | 32 | $0.50 | awaiting explicit authorization |
| SWE-ContextBench | four-target operational tranche, then conditional n=12 | 12 then at most 36 task runs | not frozen | not frozen | blocked, not authorizable |
| DeepSWE paired memory | required 12 causal targets | 0 | 0 | $0 | rejected at 3/12 pairs |

The current maximum authorizable dollar liability is therefore **$0.50**, for
non-executing ForgetEval proposals only.

## Packing stop decision

Render cap 1200 recovered all eight scored retrieval cases but failed the
separately deterministic exact-`UNKNOWN` sentinel: 1/4 versus current 3/4.
That free non-regression gate is binding, so the $8 reader/judge campaign is
not authorizable. A new packing technique must first pass both free retrieval
and exact-abstention predicates on the frozen small set.

## Deleted packing execution surface

The rejected packet now binds only the frozen case manifest, free diagnostic
artifacts, and retained adapter/runtime proof. The dormant paid runner,
bootstrap, meter, campaign controller, and analyzer were deleted. There is no
packing command, model selection, or spend ceiling to authorize. A materially
different technique must pass both free gates before a new paid campaign is
designed and reviewed.

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
