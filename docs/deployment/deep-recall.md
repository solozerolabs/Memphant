# Deep recall deployment

Deep is an explicit, opt-in recall mode for difficult queries. Fast remains the default, and MemPhant never auto-escalates a request into Deep.

## Data egress and privacy

Setting `MEMPHANT_DEEP=on` authorizes the serving process to send the query, the complete bodies of policy-authorized episode/resource sources, and their current compiled state to OpenRouter for processing by Azure. Compiled-state rows contain only the unit ID, kind, fact key, predicate, body, confidence, trust level, observation time, valid-time bounds, transaction start, and raw-source kind/ID; tenant, subject, scope, agent, actor, internal source reference, and scheduling metadata are omitted. The workspace exists only in memory; the agent has no shell, web, arbitrary filesystem, write, or memory-mutation tool.

Every model request requires Azure routing, Zero Data Retention, denied provider data collection, and support for every requested parameter. ZDR limits retention; it does **not** guarantee geographic residency. Workloads with residency requirements need a separately verified regional route and must leave Deep off until that route is approved.

Cancellation drops the streaming HTTP request so Azure can stop processing and billing. A cancelled generation whose final provider usage cannot be reconciled is reported with an explicit unsettled token/spend upper bound; MemPhant never reports that possible charge as zero.

## Configuration

Deep is off when `MEMPHANT_DEEP` is unset or exactly `off`. The only enabled value is exact `on`; other values fail startup. When enabled, set:

- `OPENROUTER_API_KEY`
- `MEMPHANT_DEEP_MODEL` to the exact immutable model ID used for the request and later generation receipt (floating aliases such as `latest` are rejected)
- `MEMPHANT_DEEP_RESPONSE_MODEL` to the separately registered canonical model ID that OpenRouter emits in streaming events
- `MEMPHANT_DEEP_PROMPT_PATH` to an immutable prompt file
- `MEMPHANT_DEEP_PROVIDERS=azure`
- `MEMPHANT_DEEP_INPUT_PRICE_MICROS_PER_MILLION`
- `MEMPHANT_DEEP_OUTPUT_PRICE_MICROS_PER_MILLION`

`MEMPHANT_DEEP_OPENROUTER_BASE_URL` defaults to `https://openrouter.ai/api/v1`. An override is a durable private/regional gateway contract: MemPhant sends that gateway the API key, query, and authorized source bodies, so the operator must verify that it preserves OpenRouter's streaming, generation-metadata, ZDR, data-deny, and Azure-routing semantics. Overrides must use HTTPS; HTTP is accepted only for a loopback test gateway. Credentials, query strings, and fragments in the URL are rejected. The endpoint is covered by the config hash.

That validated endpoint is the complete egress boundary. The Deep HTTP client disables redirects, ignores ambient/system proxy configuration, and disables reqwest's implicit protocol retries. MemPhant's explicit retry loop is therefore the only replay mechanism, and it runs only for a proven pre-generation 429/5xx response without a generation ID. Redirect, proxy, and implicit-retry policy are immutable facts covered by the config hash. If a deployment needs a proxy later, it must become explicit validated configuration with provenance rather than inheriting process or host settings.

The shipped operating point is one 120-second wall deadline, at most 24 executed tool calls, 96,000 cumulative provider input tokens, USD 0.30 maximum spend, and 4,096 maximum completion tokens. Retries, tool turns, and final usage reconciliation share those ceilings. Each current dispatch owns its reservation and optional validated generation ID, so settlement can never reuse an earlier turn's ID or erase a later unknown charge.

The frozen Azure route does not advertise the optional `parallel_tool_calls` request parameter, so strict `require_parameters` routing cannot send it. OpenRouter's provider-default response may therefore contain multiple tool calls. MemPhant accepts only contiguous indexed calls, executes them deterministically in provider order, returns one matching tool message per call, and counts every call against the same 24-tool ceiling. This keeps the route fail closed while avoiding unnecessary serial model round trips.

The request model and streamed response model are separate fail-closed contracts: the request pins an immutable snapshot, OpenRouter streaming may identify that snapshot by its canonical alias, and the post-response generation receipt must identify the exact snapshot. Both model IDs, along with provider, prompt, prices, limits, transport endpoints, egress/replay policy, retry policy, and tool-output bounds, are construction-time facts covered by the prompt/config hashes.

Only server and MCP recall services install the provider through `build_service`. The worker never reads this configuration or sends source data to a model.
