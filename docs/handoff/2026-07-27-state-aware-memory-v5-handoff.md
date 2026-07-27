# State-aware memory v5 campaign handoff

Current STATUS mirror: RUNTIME COMPLETE — BENCHMARK EVIDENCE PENDING

Date: 2026-07-27
Repository: `/Users/sidsharma/Memphant`
Branch: `main` (landed from `codex/memphant-state-memory-sota`)
HEAD at authorization: `0b72b9fa` (`docs: authorize v5 state-memory campaign`)
State: **PAUSED AFTER THE AUTHORIZED RUN — DO NOT BLINDLY RESUME**

## Executive state

The v5 request-local alias protocol is implemented, reviewed, tested, censused,
and authorized. Its 64-plan paid construction canary passed cleanly: 64/64
decoded, zero semantic or incomplete failures, zero retries, and a one-sided
exact 95% Clopper-Pearson failure-rate upper bound of
`0.04572970233076246063023830436`, below the frozen `0.15` gate. Canary cost
was `$0.23566915`.

The broader prefix construction did **not** complete. The single authorized
runner exited 1 after OpenRouter's DeepInfra-pinned Qwen route returned large
bursts of HTTP 429. The official reader, judge, native scorer, and SOTA
comparison never ran. No default, STATUS checkbox, deployment, or SOTA claim
is earned.

Paid work is paused. No retry or follow-on campaign was launched after the
failure.

## What was implemented and frozen

Commits, oldest to newest:

1. `19019e7c` — seal v4 campaign and pivot to v5.
2. `10d4227f` — include every historical v4 canary failure.
3. `817be133` — bind structured evidence to request-local aliases.
4. `62f11436` — update v5 structured-census contract tests.
5. `d7ff5da6` — carry v4 failed plans across extraction-version keys by the
   stable `(source_body_sha256, batch_index)` identity.
6. `4cb063cc` — freeze the v5 census.
7. `0b72b9fa` — authorize the v5 campaign.

The protocol sends aliases `s000..s999` plus evidence bodies to the model,
keeps canonical IDs and source spans off the model wire, constrains evidence
IDs with a dynamic enum, and admits only exact uniquely grounded quotes. The
implementation has no repair pass or hidden retry.

Frozen identities:

- Census SHA-256: `58aef24a14abbb41ff2a67ab8c3a42f0c4ca55299c7528a63bb1be772d1dae34`
- Census file SHA-256: `800009eab04a11de7457a2d8e014fd6ebf57189e1fd532a7001e6cb72f8bdbe6`
- Authorization scope SHA-256: `71a388d99597481e99b524cb8fd289b59fd0e646766ca5fd29ba6b9094449c1c`
- Authorization file SHA-256: `311f318bf7de0a22b4524dd514881d715d4ba9900b8a394798ea0e9da90dfb86`
- Frozen inventory: 11,704 construction plans.
- Hard campaign ceiling: `$200.00`.
- Opening historical liability: `$5.72313825`.
- Aggregate construction reservation: `$74.10827160`.
- Unallocated reserve: `$10.00`.

The canary deliberately contains all six exact v4 failed plans once, plus the
frozen stratified sample. Its gate SHA-256 is
`2942702963762a57e127c5133f78eda2399f4864e7869dfaeaa216d744689347`.

## Terminal run reconciliation

Artifact root:

`docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot-v5`

The construction ledger covers the 64 canary plans and 10,131 prefix plans:

| Population | Starts | Terminal results | HTTP 200 | HTTP 429 | Nonterminal |
|---|---:|---:|---:|---:|---:|
| Canary | 64 | 64 | 64 | 0 | 0 |
| Prefix | 10,131 | 10,130 | 7,352 | 2,778 | 1 |
| Combined | 10,195 | 10,194 | 7,416 | 2,778 | 1 |

Every HTTP 200 result is settled and decoded. Every HTTP 429 result has no
recorded usage or response ID, but the runtime deliberately records it as
`reservation_status="unresolved"`; it is **not proven unbilled**. The unmatched
start is extraction key
`2cdee453a3e7017537629ee8cf022d5a0810e7ddb4f50c551a34bc2c6473c189`.
It has a captured `response.json` dispatch journal and no terminal ledger
result. It must be resumed from that captured response, never redispatched
blindly.

Accounting at pause:

- Known settled v5 construction usage: `$27.12925840`.
- Of that, canary usage: `$0.23566915`.
- Prefix settled usage: `$26.89358925`.
- Maximum liability retained by 2,778 unresolved 429s: `$15.17563190`.
- Maximum liability retained by the one nonterminal captured response:
  `$0.00635000`.
- Opening liability + settled v5 usage + outstanding construction liability:
  `$48.03437855`, below the `$200` hard ceiling.

Terminal public hashes:

- `CONSTRUCTION-ATTEMPTS.jsonl`:
  `caa077a356d827cfa9c13e793dab80692ee298405198d878bd8cb0fa06e3d5d2`
- `CAMPAIGN-ATTEMPTS.jsonl`:
  `f5ed3aec6a34b50a320bcf69225c3a64d3188d3d29607d5ccd861626c326e03d`
- `CONSTRUCTION-WAVE.json`:
  `592ce02115f3549fbf1ab6c06de457f9e6087331976d5bb04259e97e6b9263b6`
- `CONSTRUCTION-CANARY-GATE.json` file:
  `113b970c94c41e12eb051b57a6f92018894b823df15d212b8f215ea415931c2b`
- `CONSTRUCTION-CANARY-PROGRESS.json`:
  `04e7f0593d5a129b06603c6d7c37067b8b27ee32a020cec8e3d3d4fb28fe3df6`
- `PREFIX-12-CONSTRUCTION-PLANS.json`:
  `788133496721006013185aa4bcfb45dcaf0e3d5aa90cb960cfa9819d70dd7eb7`

`CONSTRUCTION-PROGRESS.json` was never created. The aggregate campaign ledger
contains a start and no settlement. No construction retry artifact, reader
row, judge row, official metric, native package, closure, or external-SOTA
artifact exists.

Do not commit the local construction resources, observation cache, case banks,
or private dispatch/reader/judge directories. They contain private benchmark
material or provider payloads. Preserve them locally for exact resume and
adjudication.

## Root cause

The terminal error was:

```text
structured-state execute failed 2779 plans: structured-state provider unavailable:
OpenRouter HTTP 429: qwen/qwen3.5-9b is temporarily rate-limited upstream
```

The failure is not a v5 semantic/schema failure. It is an execution-contract
failure with three linked causes:

1. `OpenRouterStructuredState` always posts to OpenRouter. The Qwen request
   pins `only: ["deepinfra"]` and `allow_fallbacks: false`; therefore the
   campaign depends on OpenRouter's shared DeepInfra capacity even though
   cache metadata names the served provider `DeepInfra`.
2. The Rust `structured-state execute` command processes the full subset and
   exits nonzero if any plan fails. Python's `execute_subset` converts that
   nonzero exit into an exception, so control never reaches the separately
   prepaid retry-wave orchestration.
3. Even if control reached the retry planner, `_failed_construction_plans`
   rejects every `reservation_status="unresolved"` result as ambiguous and
   non-dispatchable. OpenRouter 429s are intentionally recorded as unresolved,
   not `not_charged`, so the retry path is unreachable for this live failure.

There was also no condition-based capacity pause between 429 bursts. Hidden
in-process retries are correctly forbidden, but wave-level retry scheduling
needs an explicit durable wait/adjudication contract.

Current official OpenRouter guidance says 429 is a request- or token-level
rate limit and SDKs should respect retry/backoff headers. It also documents
that provider fallbacks improve availability and that BYOK provides direct
control over provider rate limits. Those are research inputs, not permission
to change this frozen provider identity mid-campaign:

- <https://openrouter.ai/docs/api/reference/errors-and-debugging>
- <https://openrouter.ai/docs/guides/routing/provider-selection>
- <https://openrouter.ai/docs/guides/overview/auth/byok>

## What the evidence does and does not prove

Supported finding:

> On the frozen 64-plan construction canary, the v5 request-local alias
> protocol produced 64/64 decoded, semantically valid results with zero
> retries. With zero observed failures, the one-sided exact 95% failure-rate
> upper bound is 4.573%, below the preregistered 15% gate.

This supports protocol reliability conditional on provider availability. It
does not establish LongMemEval-V2 accuracy, a MemPhant-vs-baseline improvement,
an official benchmark result, leaderboard placement, or SOTA.

The 7,352 successful prefix constructions are partial reusable material, not
a statistically valid official score. Do not score the survivor subset.

## Required next steps, in order

1. **Preserve and test the failure before editing.** Add a live-shaped test
   covering a 429 result with no response ID/usage, plus a captured-response
   start without a terminal ledger event. The test must prove that exact
   resume never redispatches the captured response.
2. **Fix the shared execution contract, not this artifact.** Make construction
   execution return a durable per-plan terminal summary even when some plans
   fail, so the outer campaign controller can adjudicate and schedule retries.
   Do not hide retries inside the Rust provider.
3. **Separate retry eligibility from accounting settlement.** An unresolved
   liability is not automatically non-dispatchable. Define and test explicit
   classes for: captured-response exact resume, typed not-charged capacity
   rejection, unresolved 429 with no generation identity, and truly ambiguous
   transport failure. Retain maximum liability unless provider evidence proves
   non-charge.
4. **Add condition-based wave scheduling.** Honor `Retry-After` when present;
   otherwise use a bounded exponential delay with a provider-health condition.
   Persist every wait and wave boundary. Keep per-request hidden retries at
   zero.
5. **Reconcile authorization before paying again.** The current `$10.00` retry
   pool is smaller than the `$15.18198190` outstanding maximum construction
   liability. After the code fix, run a zero-spend recensus/re-authorization
   that retains existing liability, binds the exact 2,779-plan recovery set,
   and remains below the user's `$200` total ceiling.
6. **Choose the provider path with a zero-cost probe.** Prefer, in order:
   exact same OpenRouter+DeepInfra route with durable backoff; OpenRouter BYOK
   for DeepInfra if an already-authorized key exists; or a newly frozen direct
   DeepInfra route. Allowing another provider/model is a new experiment and
   requires new tokenizer/schema/pricing census and a fresh canary.
7. **Resume construction exactly once.** First settle the captured response,
   then retry only the authorized failed set. Rebuild and hash the complete
   11,704-plan cache before opening any reader or judge work.
8. **Run official evaluation only after complete construction settlement.**
   Then verify row settlement, official metrics, paired statistical gates,
   cost, and claim boundaries. A canary or partial prefix must never be promoted
   into a SOTA claim.

## Paste-ready continuation prompt

```text
Continue MemPhant's state-aware memory program from
docs/handoff/2026-07-27-state-aware-memory-v5-handoff.md. Read it completely,
then inspect the current worktree and artifact hashes before acting. Do not
launch paid work yet. First write live-shaped failing tests for the explicit
OpenRouter 429/unresolved-liability path and the one captured-response
nonterminal start. Fix the root execution contract so per-plan failures return
durably to the outer wave controller, distinguish retry eligibility from
accounting settlement, and add durable condition-based wave backoff without
hidden per-request retries. Verify the focused and full secret-free gates.
Then produce a zero-spend recensus and new authorization for only the exact
recovery set, retaining all existing liability and staying under the user's
$200 total ceiling. Stop for review before any paid resume. Never redispatch
the captured response, inspect private response bodies, score the survivor
subset, or claim official/SOTA evidence from the 64-plan canary.
```
