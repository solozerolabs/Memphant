# MemPhant Near-SOTA Program Design

## Goal

Consolidate the repository into one authoritative, pushed trunk, then obtain a
credible near-SOTA signal on at least one public 2026 benchmark without adding a
second memory engine or mistaking a saturable retrieval proxy for product value.

## Decision

Use one Postgres-backed, bitemporal substrate. Keep deterministic Fast recall as
the default and explicit Deep recall as the accuracy-first escape hatch. Stop
optimizing keys, fusion, and local rerankers on Track R and MemoryCode: those
instruments have answered their questions and are structurally saturated.

The next product hypothesis is behavior-changing memory whose gold depends on
evidence outside the current statement set: evolving preferences, scoped rules,
negative constraints, aggregate state, and proactive application. This matches
the user-visible job better than another fact-retrieval arm.

## Evidence basis

- HorizonBench evaluates 4,245 long-horizon preference questions linked to 360
  user-level mental-state graphs. It is the strongest public preference
  candidate found, but its current dataset release and evaluator fail the
  acquisition/completeness gate: <https://arxiv.org/abs/2604.17283>.
- PERMA evaluates evolving preferences across time, domains, noise, and
  interactive task completion rather than static preference retrieval:
  <https://arxiv.org/abs/2603.23231>.
- PerMemBench's selective session-level storage gate reinforces the product
  seam, but its released study has only 20 users and cannot resolve this
  program's 3--7 point target: <https://arxiv.org/abs/2605.25535>.
- Proactive Memory Agent reports behavior gains from selectively applying
  memory at the agent edge, supporting an intervention policy above the store
  rather than another storage engine: <https://arxiv.org/abs/2607.08716>.
- User as Code shows why executable aggregate state and proactive rules can beat
  retrieval-only memory, but MemPhant must express that value through its
  existing typed units, lineage, and projections rather than adding a Python
  state engine: <https://arxiv.org/abs/2606.16707>.
- MemoryAgentBench separates retrieval, test-time learning, long-range
  understanding, and selective forgetting. MemPhant claims must remain split by
  these capabilities: <https://mlanthology.org/iclr/2026/hu2026iclr-evaluating/>.

## Architecture

```mermaid
flowchart LR
  E["Episodes and resources"] --> P["One Postgres substrate"]
  P --> U["Typed current units and lineage"]
  U --> F["Fast: deterministic cited evidence"]
  U --> D["Deep: explicit bounded search"]
  F --> B["Behavior-level benchmark"]
  D --> B
  B --> G{"Near-SOTA gate"}
  G -->|"pass"| C["Candidate for public claim"]
  G -->|"fail"| R["Retire lever; preserve proof"]
```

No SQLite, graph database, vector service, executable Python state store, or
benchmark-specific mutation path is added. `InMemoryStore` remains a test fake,
not a second production substrate or a basis for storage claims.

## User experience contract

Accuracy and user-visible correctness dominate every other axis. The preferred
experience is:

1. The agent remembers a durable rule without the user repeating it.
2. Corrections replace the old rule without resurrecting it.
3. Scoped exceptions apply only where intended.
4. Negative constraints prevent the wrong action.
5. The agent can proactively surface a relevant constraint.
6. Every applied memory has a correction handle and verifiable receipt.
7. Fast remains the default; Deep is explicit, bounded, cancellable, and uses
   the same cited-evidence response contract.

Cost and latency are optimization constraints after correctness. The target is
positive official behavior-level lift or accuracy within three percentage
points of the reverified best comparable system at materially lower
latency/cost. Security is never removed, but pre-production work does not spend
cycles on speculative hardening ahead of the behavior gate.

## Workstreams and order

### A. Consolidate and publish

Land complete S1b and MiniLM negative evidence, recover nothing from the fully
contained stash, remove non-input scratch files and transient logs, run the full
repo gate, and push the single trunk. Preserve benchmark caches outside the repo.

### B. Acquire a discriminating preference instrument

Audit PERMA's exact license, immutable revision, data shape, and evaluator. Build
only a Stage-0 adapter round trip first. The adapter must map public events to the
existing retain/correct/recall contracts and must score end behavior. It may not
derive gold from the same statements offered to the retriever.

PERMA and HorizonBench both failed before adapter work. PERMA lacks license
artifacts, user/history lineage in its packaged rows, sufficient independent
units, and a fail-closed scorer. HorizonBench has the right linked user/history
shape and 360 independent clusters, but its dataset license is card-only and its
runner drops failed rows and bootstraps rows rather than users. HorizonBench is
the preferred reopen target because its scientific shape is stronger.

The private Track U bank remains a coarse smoke only. Its 51 goldens cannot
resolve a 7-point effect; it may support a preregistered roughly 15-point screen
only if independent units can reach the required count without duplicating a
source file.

### C. Keep the retired environment-memory path closed

Do not resume LongMemEval-V2 v5. Commit `17700a381ba17a725182429250b7bf3f9ad09045`
deleted its harness after the instrument register found zero official outputs;
v1/v3/v4 are `ABANDONED_NEVER_RESUME`, and the retained maximum is explicitly
not recoverable by resuming. The July 27 partial construction ledger remains
provenance only. Reopening requires a newly acquired complete instrument and a
fresh plan, not restoration of deleted apparatus.

## Gates

| Gate | Advance when | Stop when |
|---|---|---|
| Repository | Full verification passes and `origin/main` equals local main | Any in-scope failure or unresolved branch/stash lineage |
| Instrument | License artifact pinned, gold external to statement set, Stage-0 round trip exact, power reachable | Card-only license, saturable gold, or unreachable sample size |
| Preference smoke | End-behavior gain clears preregistered effect and no scope/negative-constraint regression | CI crosses zero or safety/correction behavior regresses |
| Retired lane | A new licensed, complete instrument independently qualifies | Restoring LongMemEval-V2's deleted apparatus or scoring its partial survivor bank |
| Near-SOTA | Positive LAFS contribution or within 3pp accuracy at materially better cost/latency | Comparator remains clearly superior on accuracy and operating cost |

## Status ownership

`docs/superpowers/specs/memphant/STATUS.md` remains the only state ledger. It
will carry a compact current dashboard across surfaces, memory kinds, storage,
and the keys-to-reader pipeline. Proof and contracts stay in their existing
owner documents.

## Non-goals

- No more threshold, fusion, embedding, or reranker arms on Track R or
  MemoryCode without a new discriminating instrument.
- No global or storage SOTA claim.
- No migration of Syndai surfaces before a behavior-level gate wins.
- No new dependency or service for a capability the existing runtime provides.
