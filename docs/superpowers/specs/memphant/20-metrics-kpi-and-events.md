# MemPhant - Metrics, KPI, and Events

## 0. North Star

Weekly Active Memory Consumers (WAMC):

> `WAMC = COUNT(DISTINCT project_id WHERE successful recall ≥ 1 AND successful retain ≥ 1 IN last 7d)` — distinct external projects exercising both write and read in a rolling 7-day window. "Successful" = HTTP 2xx and (for recall) `degraded = false`.

## 1. Product Metrics

- weekly active projects
- MCP installs
- SDK downloads
- retain calls
- recall calls
- correct calls
- trace views
- eval runs
- successful quickstarts
- time to first recall

## 2. Quality Metrics

- recall@k
- precision@k
- citation validity
- answer accuracy
- stale fact suppression
- correction supersession correctness
- forget completeness
- poisoning injection success rate
- cross-tenant leakage count
- p50/p95 recall latency
- cost per recall
- memory utility trend (rollup over `mark.recorded` events, §3; the `22` §1.3 SLI)

## 2.1 Lever Metrics

Each feature flag gets per-eval deltas:

```text
delta_recall_at_k
delta_citation_validity
delta_latency_ms
delta_cost_micros
delta_context_tokens
delta_poisoning_success
```

This is how the team knows which lever to pull when benchmarks miss.

The ordered lever ladder and promotion rules live in `27-sota-ladder-and-validation.md`.

## 3. Event Taxonomy

```text
memphant.project_created
memphant.api_key_created
memphant.retain_called
memphant.recall_called
memphant.reflect_requested
memphant.correct_called
memphant.forget_called
memphant.trace_viewed
memphant.eval_run_started
memphant.eval_run_completed
memphant.poisoning_fixture_failed
memphant.delete_check_failed
```

**Reserved event types (consolidation lifecycle + outcome feedback) — reserved now, built post-v1; the emitting surface is owned by `08`:**

```text
memory.promoted                 # belief -> semantic promotion committed
memory.superseded               # a correction/supersession generation applied
                                # (emitted for `semantic` AND `preference`; the
                                #  arm that closed the generation is on the row)
memory.contradiction_detected   # write-path contradiction contract fired
memory.quarantined              # write- or read-time quarantine entry
reflect.completed               # a reflect cycle finished for a subject
mark.recorded                   # outcome feedback on recalled memory (the `mark` verb)
```

Each reserved event carries `{tenant_id, scope_id, memory_unit_id(s), generation/trust refs, occurred_at}` on top of the §4 common envelope. Delivery is **transactional outbox + poll-cursor first** (webhooks later): the event row commits in the same transaction as the state change it reports, so a consumer never sees an event for a state that did not durably happen. `mark.recorded` records outcome feedback with the closed vocabulary `success|failure|corrected|ignored`; outcome labels are **tenant data, not product analytics** (`06` §1 invariant #10 — they describe what a tenant's agent did) and stay out of the analytics plane per §7.

## 4. Event Fields

Common envelope (every event carries it; `event_schema_version` makes the taxonomy evolvable):

```text
event_id
event_schema_version
timestamp
tenant_id
project_id
actor_type
request_id
trace_id            # bridges product events to the retrieval trace
sdk_name
sdk_version
server_version
visibility          # 'public' (publishable aggregate) | 'internal' (operator-only) | 'billing' (tenant-attributed metered unit, immutable — the source of truth for the 21 §1a units, kept separate from adoption analytics)
```

Each event also declares a **fire-condition** (the exact moment it emits) and a **visibility** class so the analytics plane never leaks internal signal. Schema evolution is **additive-only**: never remove or repurpose a field; add a new one and bump `event_schema_version`.

Recall:

```text
retrieval_id
engine_version
feature_flags
candidate_count
returned_count
latency_ms
estimated_cost_usd
degraded            # true when served in consolidation-lag fallback (02 §3.1)
consolidation_lag_ms
filter_selectivity  # small-tenant HNSW visibility (02 §2.1b)
```

Trace/eval:

```text
retrieval_id
trace_schema_version
eval_run_id
eval_case_id
benchmark_id
benchmark_version
feature_flags
expected_memory_ids
returned_memory_ids
citation_valid
forbidden_leak_count
outcome
```

Security:

```text
security_suite
threat_kind
blocked
policy_version
cross_tenant_result_count
deletion_generation
```

## 5. Launch KPIs

Public launch:

- 100 GitHub stars
- 25 successful external quickstarts
- 10 external eval runs
- 3 non-Syndai pilots
- 0 known tenant/deletion/security criticals

These are directional, not vanity absolutes. Quality issues override growth.

## 6. Kill/Pivot Metrics

| Metric | Action |
|---|---|
| golden recall below internal Syndai contract threshold | fix basics before external launch |
| external sampled benchmark no better than baseline after 3 lever cycles | narrow claim or pivot to trace/security harness |
| any tenant/deletion critical | stop launch work until fixed |
| high adoption but low eval pass rate | improve docs/examples/eval feedback before GTM spend |
| high trace views per recall | recall quality or UX clarity problem |

## 7. Product Events vs Quality Facts

Product analytics answer adoption questions. Quality facts answer memory-quality questions.

Do not store raw memory text in product analytics. Retrieval traces may contain memory snippets and therefore inherit tenant data policy.

## 8. Dogfood Metrics

Syndai dogfood reports:

- export coverage
- trace-compare contract pass rate
- recall disagreement rate
- L0/L1+ access regression count
- citation render success
- correction/forget contract pass rate
- p95 backend memory latency
- mobile/web memory UX regression count
