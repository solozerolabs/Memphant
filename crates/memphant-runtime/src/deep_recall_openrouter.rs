use std::collections::{BTreeMap, HashSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use eventsource_stream::Eventsource;
use futures::{Stream, StreamExt};
use memphant_core::deep_recall::{
    DeepRecallProvider, DeepRecallProviderError, DeepRecallProviderRequest,
    DeepRecallProviderResult,
};
use memphant_types::{
    DeepProviderIdentity, DeepRecallLimits, DeepRecallStatus, DeepRecallStopReason,
    DeepRecallUsage, DeepWorkspace, EVIDENCE_DISPOSITION_CONTRACT_REVISION, EvidenceDisposition,
    EvidenceStatus,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

const MAX_LIST_RESULTS: usize = 256;
const MAX_QUERY_CHARS: usize = 256;
const MAX_SEARCH_HITS: usize = 128;
const MAX_TOOL_OUTPUT_BYTES: usize = 64 * 1024;
const MAX_EVIDENCE_IDS: usize = 256;
const MAX_READ_LINES: usize = 512;
const DEFAULT_MAX_COMPLETION_TOKENS: u64 = 4_096;
const DEFAULT_WALL_TIME_MS: u64 = 120_000;
const DEFAULT_MAX_TOOL_ITERATIONS: u32 = 24;
const DEFAULT_MAX_CONTEXT_TOKENS: u64 = 96_000;
const DEFAULT_MAX_SPEND_MICROS: u64 = 300_000;
const DEFAULT_OPENROUTER_BASE_URL: &str = "https://openrouter.ai/api/v1";
const LME_V2_QWEN_MODEL: &str = "qwen/qwen3.5-9b-20260310";

#[derive(Debug, Clone)]
pub struct DeepConfig {
    api_key: String,
    model: String,
    response_model: String,
    prompt: String,
    providers: Vec<String>,
    input_price_micros_per_million: u64,
    output_price_micros_per_million: u64,
    limits: DeepRecallLimits,
    max_completion_tokens: u64,
    completion_url: String,
    generation_url: String,
    connect_timeout_ms: u64,
    settlement_reserve_ms: u64,
    max_retries: u8,
    retry_base_ms: u64,
    malformed_response_limit: u8,
}

impl DeepConfig {
    pub fn new(
        api_key: String,
        model: String,
        response_model: String,
        prompt: String,
        providers: Vec<String>,
        input_price_micros_per_million: u64,
        output_price_micros_per_million: u64,
    ) -> Result<Self, String> {
        if api_key.trim().is_empty()
            || prompt.trim().is_empty()
            || model.trim().is_empty()
            || response_model.trim().is_empty()
        {
            return Err(
                "Deep API key, request model, response model, and prompt must not be empty"
                    .to_string(),
            );
        }
        if model.to_ascii_lowercase().contains("latest") || model.contains('*') {
            return Err("MEMPHANT_DEEP_MODEL must be an exact non-floating model id".to_string());
        }
        let required_provider = if model == LME_V2_QWEN_MODEL {
            "deepinfra"
        } else {
            "azure"
        };
        if providers.as_slice() != [required_provider] {
            return Err(format!(
                "MEMPHANT_DEEP_PROVIDERS must be exactly {required_provider} for {model}"
            ));
        }
        if input_price_micros_per_million == 0 || output_price_micros_per_million == 0 {
            return Err("Deep input and output price ceilings must be positive".to_string());
        }
        Ok(Self {
            api_key,
            model,
            response_model,
            prompt,
            providers,
            input_price_micros_per_million,
            output_price_micros_per_million,
            limits: DeepRecallLimits {
                wall_time_ms: DEFAULT_WALL_TIME_MS,
                max_tool_iterations: DEFAULT_MAX_TOOL_ITERATIONS,
                max_context_tokens: DEFAULT_MAX_CONTEXT_TOKENS,
                max_spend_micros: DEFAULT_MAX_SPEND_MICROS,
            },
            max_completion_tokens: DEFAULT_MAX_COMPLETION_TOKENS,
            completion_url: format!("{DEFAULT_OPENROUTER_BASE_URL}/chat/completions"),
            generation_url: format!("{DEFAULT_OPENROUTER_BASE_URL}/generation"),
            connect_timeout_ms: 10_000,
            settlement_reserve_ms: 5_000,
            max_retries: 2,
            retry_base_ms: 250,
            malformed_response_limit: 1,
        })
    }

    pub fn with_openrouter_base_url(mut self, base_url: &str) -> Result<Self, String> {
        let parsed = reqwest::Url::parse(base_url)
            .map_err(|error| format!("invalid OpenRouter base URL: {error}"))?;
        if !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.query().is_some()
            || parsed.fragment().is_some()
        {
            return Err(
                "OpenRouter base URL must not contain credentials, query, or fragment".into(),
            );
        }
        let host = parsed
            .host_str()
            .ok_or_else(|| "OpenRouter base URL requires a host".to_string())?;
        let loopback = host.eq_ignore_ascii_case("localhost")
            || host
                .parse::<std::net::IpAddr>()
                .is_ok_and(|address| address.is_loopback());
        if parsed.scheme() != "https" && !(parsed.scheme() == "http" && loopback) {
            return Err(
                "OpenRouter base URL requires HTTPS; HTTP is allowed only on loopback".into(),
            );
        }
        let base = parsed.as_str().trim_end_matches('/');
        self.completion_url = format!("{base}/chat/completions");
        self.generation_url = format!("{base}/generation");
        Ok(self)
    }

    #[cfg(test)]
    fn with_test_timing(mut self, wall_time_ms: u64, settlement_reserve_ms: u64) -> Self {
        self.limits.wall_time_ms = wall_time_ms;
        self.settlement_reserve_ms = settlement_reserve_ms;
        self
    }

    #[cfg(test)]
    fn with_test_retry(mut self, retry_base_ms: u64) -> Self {
        self.retry_base_ms = retry_base_ms;
        self
    }

    #[cfg(test)]
    fn with_test_caps(mut self, iterations: u32, context_tokens: u64, spend_micros: u64) -> Self {
        self.limits.max_tool_iterations = iterations;
        self.limits.max_context_tokens = context_tokens;
        self.limits.max_spend_micros = spend_micros;
        self
    }
}

type ByteStream = Pin<Box<dyn Stream<Item = Result<Vec<u8>, TransportError>> + Send>>;

struct TransportResponse {
    status: u16,
    generation_id: Option<String>,
    retry_after: Option<Duration>,
    content_type: Option<String>,
    body: ByteStream,
}

#[derive(Debug)]
struct TransportError;

impl std::fmt::Display for TransportError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("OpenRouter transport failed")
    }
}

impl std::error::Error for TransportError {}

trait Transport: Send + Sync {
    fn post<'a>(
        &'a self,
        body: &'a Value,
    ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>;
    fn generation<'a>(
        &'a self,
        id: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>;
}

#[derive(Debug, Clone, Copy)]
struct GenerationUsage {
    prompt_tokens: u64,
    cost_micros: u64,
}

struct ReqwestTransport {
    client: reqwest::Client,
    api_key: String,
    completion_url: String,
    generation_url: String,
}

impl ReqwestTransport {
    fn new(config: &DeepConfig) -> Result<Self, String> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .retry(reqwest::retry::never())
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .user_agent("memphant/0.1 deep-recall")
            .build()
            .map_err(|error| format!("failed to build Deep HTTP client: {error}"))?;
        Ok(Self {
            client,
            api_key: config.api_key.clone(),
            completion_url: config.completion_url.clone(),
            generation_url: config.generation_url.clone(),
        })
    }
}

impl Transport for ReqwestTransport {
    fn post<'a>(
        &'a self,
        body: &'a Value,
    ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>> {
        Box::pin(async move {
            let response = self
                .client
                .post(&self.completion_url)
                .bearer_auth(&self.api_key)
                .header("HTTP-Referer", "https://github.com/siddmax/memphant")
                .header("X-OpenRouter-Title", "MemPhant Deep Recall")
                .json(body)
                .send()
                .await
                .map_err(|_| TransportError)?;
            let status = response.status().as_u16();
            let generation_id = response
                .headers()
                .get("x-generation-id")
                .and_then(|value| value.to_str().ok())
                .map(str::to_string);
            let retry_after = response
                .headers()
                .get(reqwest::header::RETRY_AFTER)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<u64>().ok())
                .map(Duration::from_secs);
            let content_type = response
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .map(str::to_string);
            let body = response.bytes_stream().map(|chunk| {
                chunk
                    .map(|bytes| bytes.to_vec())
                    .map_err(|_| TransportError)
            });
            Ok(TransportResponse {
                status,
                generation_id,
                retry_after,
                content_type,
                body: Box::pin(body),
            })
        })
    }

    fn generation<'a>(
        &'a self,
        id: &'a str,
    ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>> {
        Box::pin(async move {
            let response = self
                .client
                .get(&self.generation_url)
                .bearer_auth(&self.api_key)
                .query(&[("id", id)])
                .send()
                .await
                .map_err(|_| TransportError)?;
            if !response.status().is_success() {
                return Err(TransportError);
            }
            let value: Value = response.json().await.map_err(|_| TransportError)?;
            let data = value.get("data").unwrap_or(&value);
            let prompt_tokens = data
                .get("tokens_prompt")
                .or_else(|| data.pointer("/usage/prompt_tokens"))
                .and_then(Value::as_u64)
                .ok_or(TransportError)?;
            let cost_micros = data
                .get("total_cost")
                .or_else(|| data.get("cost"))
                .or_else(|| data.pointer("/usage/cost"))
                .and_then(cost_value_micros)
                .ok_or(TransportError)?;
            Ok(GenerationUsage {
                prompt_tokens,
                cost_micros,
            })
        })
    }
}

pub struct OpenRouterDeepRecall {
    config: DeepConfig,
    identity: DeepProviderIdentity,
    transport: Arc<dyn Transport>,
}

impl OpenRouterDeepRecall {
    pub fn new(config: DeepConfig) -> Result<Self, String> {
        let transport = Arc::new(ReqwestTransport::new(&config)?);
        Ok(Self::with_transport(config, transport))
    }

    fn with_transport(config: DeepConfig, transport: Arc<dyn Transport>) -> Self {
        let prompt_hash = sha256(config.prompt.as_bytes());
        let config_hash = sha256(
            serde_json::to_vec(&json!({
                "model": config.model,
                "response_model": config.response_model,
                "providers": config.providers,
                "input_price_micros_per_million": config.input_price_micros_per_million,
                "output_price_micros_per_million": config.output_price_micros_per_million,
                "limits": config.limits,
                "max_completion_tokens": config.max_completion_tokens,
                "completion_url": config.completion_url,
                "generation_url": config.generation_url,
                "connect_timeout_ms": config.connect_timeout_ms,
                "settlement_reserve_ms": config.settlement_reserve_ms,
                "max_retries": config.max_retries,
                "retry_base_ms": config.retry_base_ms,
                "implicit_protocol_retries": "disabled",
                "redirects": "disabled",
                "ambient_proxies": "disabled",
                "request_contract": {
                    "stream": true,
                    "tool_choice": "required",
                    "parallel_tool_calls": "provider_default",
                    "parallel_tool_call_execution": "bounded_index_order",
                    "provider_require_parameters": true,
                },
                "tool_limits": {
                    "list_results": MAX_LIST_RESULTS,
                    "query_chars": MAX_QUERY_CHARS,
                    "search_hits": MAX_SEARCH_HITS,
                    "output_bytes": MAX_TOOL_OUTPUT_BYTES,
                    "read_lines": MAX_READ_LINES,
                    "evidence_ids": MAX_EVIDENCE_IDS,
                    "malformed_responses": config.malformed_response_limit,
                }
            }))
            .expect("Deep config serializes")
            .as_slice(),
        );
        Self {
            identity: DeepProviderIdentity {
                provider: config.providers.join(","),
                model: config.model.clone(),
                prompt_hash,
                config_hash,
            },
            config,
            transport,
        }
    }

    fn request_body(&self, messages: &[Value]) -> Value {
        let mut provider = json!({
            "only": self.config.providers,
            "allow_fallbacks": false,
            "require_parameters": true,
            "data_collection": "deny",
            "zdr": true,
            "max_price": {
                "prompt": dollars_per_million(self.config.input_price_micros_per_million),
                "completion": dollars_per_million(self.config.output_price_micros_per_million),
            }
        });
        if self.config.model == LME_V2_QWEN_MODEL {
            provider["quantizations"] = json!(["bf16"]);
        }
        json!({
            "model": self.config.model,
            "messages": messages,
            "max_completion_tokens": self.config.max_completion_tokens,
            "stream": true,
            "tool_choice": "required",
            "tools": tool_definitions(),
            "provider": provider
        })
    }

    async fn gather_inner(
        &self,
        request: DeepRecallProviderRequest,
    ) -> Result<DeepRecallProviderResult, DeepRecallProviderError> {
        let started = std::time::Instant::now();
        let deadline =
            tokio::time::Instant::now() + Duration::from_millis(self.config.limits.wall_time_ms);
        let loop_deadline = deadline
            .checked_sub(Duration::from_millis(self.config.settlement_reserve_ms))
            .unwrap_or(deadline);
        let mut state = LoopState::new(request, &self.config)?;
        let outcome =
            tokio::time::timeout_at(loop_deadline, self.run_loop(&mut state, loop_deadline)).await;
        let mut result = match outcome {
            Ok(Ok(result)) => result,
            Ok(Err(error)) => return Err(error),
            Err(_) => state.result(
                DeepRecallStatus::Capped,
                DeepRecallStopReason::WallTime,
                None,
            ),
        };
        if state
            .outstanding
            .as_ref()
            .and_then(|outstanding| outstanding.generation_id.as_ref())
            .is_some()
        {
            self.settle_outstanding(&mut state, deadline).await;
            result = state.result(
                result.status,
                result.stop_reason,
                Some(FinishOutcome {
                    source_ids: result.source_ids,
                    evidence: result.evidence,
                }),
            );
        }
        result.usage.wall_time_ms = elapsed_millis(started.elapsed());
        Ok(result)
    }

    async fn run_loop(
        &self,
        state: &mut LoopState,
        deadline: tokio::time::Instant,
    ) -> Result<DeepRecallProviderResult, DeepRecallProviderError> {
        loop {
            if let Some(reason) = state.stop_reason(&self.config.limits) {
                return Ok(state.result(DeepRecallStatus::Capped, reason, None));
            }
            let body = self.request_body(&state.messages);
            let reservation = match reservation_for(&body, &self.config) {
                Some(reservation) => reservation,
                None => {
                    return Ok(state.result(
                        DeepRecallStatus::Capped,
                        DeepRecallStopReason::Spend,
                        None,
                    ));
                }
            };
            if state
                .usage
                .spend_micros
                .checked_add(reservation.spend_micros)
                .is_none_or(|value| value > self.config.limits.max_spend_micros)
            {
                return Ok(state.result(
                    DeepRecallStatus::Capped,
                    DeepRecallStopReason::Spend,
                    None,
                ));
            }
            if state
                .usage
                .context_tokens
                .checked_add(reservation.context_tokens)
                .is_none_or(|value| value > self.config.limits.max_context_tokens)
            {
                return Ok(state.result(
                    DeepRecallStatus::Capped,
                    DeepRecallStopReason::ContextTokens,
                    None,
                ));
            }

            let mut attempts = 0usize;
            let response = loop {
                attempts += 1;
                state.begin_dispatch(reservation);
                let response =
                    match tokio::time::timeout_at(deadline, self.transport.post(&body)).await {
                        Ok(Ok(response)) => response,
                        Ok(Err(_)) => {
                            return Ok(state.result(
                                DeepRecallStatus::Partial,
                                DeepRecallStopReason::ProviderError,
                                None,
                            ));
                        }
                        Err(_) => {
                            return Ok(state.result(
                                DeepRecallStatus::Capped,
                                DeepRecallStopReason::WallTime,
                                None,
                            ));
                        }
                    };
                if (response.status == 429 || response.status >= 500)
                    && response.generation_id.is_none()
                    && attempts <= usize::from(self.config.max_retries)
                {
                    state.clear_current_dispatch();
                    let delay = response
                        .retry_after
                        .unwrap_or(Duration::from_millis(self.config.retry_base_ms));
                    if tokio::time::timeout_at(deadline, tokio::time::sleep(delay))
                        .await
                        .is_err()
                    {
                        return Ok(state.result(
                            DeepRecallStatus::Capped,
                            DeepRecallStopReason::WallTime,
                            None,
                        ));
                    }
                    continue;
                }
                break response;
            };

            if !(200..300).contains(&response.status) {
                if response.generation_id.is_none() {
                    let has_prior_work = state.has_prior_work();
                    state.clear_current_dispatch();
                    if has_prior_work {
                        return Ok(state.result(
                            DeepRecallStatus::Partial,
                            DeepRecallStopReason::ProviderError,
                            None,
                        ));
                    }
                    return Err(DeepRecallProviderError::Unavailable);
                }
                if state
                    .attach_generation(response.generation_id.as_deref().unwrap())
                    .is_err()
                {
                    return Ok(state.result(
                        DeepRecallStatus::Partial,
                        DeepRecallStopReason::InvalidOutput,
                        None,
                    ));
                }
                return Ok(state.result(
                    DeepRecallStatus::Partial,
                    DeepRecallStopReason::ProviderError,
                    None,
                ));
            }
            if response
                .generation_id
                .as_deref()
                .is_none_or(|id| id.trim().is_empty())
            {
                return Ok(state.result(
                    DeepRecallStatus::Partial,
                    DeepRecallStopReason::InvalidOutput,
                    None,
                ));
            }
            if state
                .attach_generation(response.generation_id.as_deref().unwrap())
                .is_err()
            {
                return Ok(state.result(
                    DeepRecallStatus::Partial,
                    DeepRecallStopReason::InvalidOutput,
                    None,
                ));
            }
            if !response.content_type.as_deref().is_some_and(|value| {
                value.split(';').next().is_some_and(|media_type| {
                    media_type.trim().eq_ignore_ascii_case("text/event-stream")
                })
            }) {
                return Ok(state.result(
                    DeepRecallStatus::Partial,
                    DeepRecallStopReason::InvalidOutput,
                    None,
                ));
            }
            let turn = match tokio::time::timeout_at(deadline, parse_turn(response.body)).await {
                Ok(Ok(turn)) => turn,
                Ok(Err(reason)) => {
                    return Ok(state.result(DeepRecallStatus::Partial, reason, None));
                }
                Err(_) => {
                    return Ok(state.result(
                        DeepRecallStatus::Capped,
                        DeepRecallStopReason::WallTime,
                        None,
                    ));
                }
            };
            if state
                .observe_route(&turn, &self.config.providers, &self.config.response_model)
                .is_err()
                || state.settle(turn.prompt_tokens, turn.cost_micros).is_err()
            {
                return Ok(state.result(
                    DeepRecallStatus::Partial,
                    DeepRecallStopReason::InvalidOutput,
                    None,
                ));
            }
            let call_count = u32::try_from(turn.tool_calls.len())
                .map_err(|_| DeepRecallProviderError::InvalidOutput)?;
            let Some(tool_iterations) = state.usage.tool_iterations.checked_add(call_count) else {
                return Err(DeepRecallProviderError::InvalidOutput);
            };
            if tool_iterations > self.config.limits.max_tool_iterations {
                return Ok(state.result(
                    DeepRecallStatus::Capped,
                    DeepRecallStopReason::ToolIterations,
                    None,
                ));
            }
            state.usage.tool_iterations = tool_iterations;

            let mut assistant_calls = Vec::with_capacity(turn.tool_calls.len());
            let mut tool_messages = Vec::with_capacity(turn.tool_calls.len());
            let mut finished = None;
            for call in turn.tool_calls {
                let args = match serde_json::from_str::<Value>(&call.arguments) {
                    Ok(args) => args,
                    Err(_) => json!(null),
                };
                let tool = state.tools.call(&call.tool_name, args);
                if tool.content.get("error").is_some() {
                    state.malformed_responses += 1;
                    if state.malformed_responses > self.config.malformed_response_limit {
                        return Ok(state.result(
                            DeepRecallStatus::Partial,
                            DeepRecallStopReason::InvalidOutput,
                            None,
                        ));
                    }
                }
                assistant_calls.push(json!({
                    "id": call.call_id,
                    "type": "function",
                    "function": {"name": call.tool_name, "arguments": call.arguments}
                }));
                tool_messages.push(json!({
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "content": tool.content.to_string()
                }));
                finished = finished.or(tool.finish);
            }
            state.messages.push(json!({
                "role": "assistant",
                "reasoning_details": turn.reasoning_details,
                "tool_calls": assistant_calls
            }));
            state.messages.extend(tool_messages);
            if let Some(finish) = finished {
                return Ok(state.result(
                    DeepRecallStatus::Completed,
                    DeepRecallStopReason::Completed,
                    Some(finish),
                ));
            }
        }
    }

    async fn settle_outstanding(&self, state: &mut LoopState, deadline: tokio::time::Instant) {
        let Some(id) = state
            .outstanding
            .as_ref()
            .and_then(|outstanding| outstanding.generation_id.clone())
        else {
            return;
        };
        let settlement_deadline = deadline.min(
            tokio::time::Instant::now() + Duration::from_millis(self.config.settlement_reserve_ms),
        );
        if let Ok(Ok(usage)) =
            tokio::time::timeout_at(settlement_deadline, self.transport.generation(&id)).await
        {
            let _ = state.settle(usage.prompt_tokens, usage.cost_micros);
        }
    }
}

impl DeepRecallProvider for OpenRouterDeepRecall {
    fn identity(&self) -> &DeepProviderIdentity {
        &self.identity
    }

    fn limits(&self) -> DeepRecallLimits {
        self.config.limits
    }

    fn gather<'a>(
        &'a self,
        request: DeepRecallProviderRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<DeepRecallProviderResult, DeepRecallProviderError>>
                + Send
                + 'a,
        >,
    > {
        Box::pin(self.gather_inner(request))
    }
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn dollars_per_million(micros: u64) -> Value {
    if micros.is_multiple_of(1_000_000) {
        return json!(micros / 1_000_000);
    }
    serde_json::Number::from_f64(micros as f64 / 1_000_000.0)
        .map(Value::Number)
        .expect("finite configured price")
}

fn tool_definitions() -> Value {
    json!([
        {"type":"function","function":{"name":"list_files","description":"List visible workspace files","parameters":{"type":"object","properties":{"prefix":{"type":["string","null"]}},"additionalProperties":false}}},
        {"type":"function","function":{"name":"search_files","description":"Literal case-insensitive source search","parameters":{"type":"object","properties":{"query":{"type":"string"},"path_prefix":{"type":["string","null"]}},"required":["query"],"additionalProperties":false}}},
        {"type":"function","function":{"name":"read_file","description":"Read an inclusive line range","parameters":{"type":"object","properties":{"path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["path","start_line","end_line"],"additionalProperties":false}}},
        {"type":"function","function":{"name":"record_evidence","description":"Checkpoint source UUIDs","parameters":{"type":"object","properties":{"source_ids":{"type":"array","items":{"type":"string"}}},"required":["source_ids"],"additionalProperties":false}}},
        {"type":"function","function":{"name":"finish","description":"Finish with ordered source UUIDs and a calibrated evidence disposition","parameters":{"type":"object","properties":{"source_ids":{"type":"array","items":{"type":"string"}},"evidence_status":{"type":"string","enum":["supported","contradicts-premise","near-match","insufficient"]},"reason":{"type":"string","minLength":1,"maxLength":256}},"required":["source_ids","evidence_status","reason"],"additionalProperties":false}}}
    ])
}

#[derive(Debug, Clone, Copy)]
struct Reservation {
    context_tokens: u64,
    spend_micros: u64,
}

struct OutstandingRequest {
    reservation: Reservation,
    generation_id: Option<String>,
}

struct LoopState {
    messages: Vec<Value>,
    tools: WorkspaceTools,
    usage: DeepRecallUsage,
    outstanding: Option<OutstandingRequest>,
    generation_ids: Vec<String>,
    observed_provider: Option<String>,
    observed_model: Option<String>,
    malformed_responses: u8,
}

impl LoopState {
    fn new(
        request: DeepRecallProviderRequest,
        config: &DeepConfig,
    ) -> Result<Self, DeepRecallProviderError> {
        let DeepRecallProviderRequest { query, workspace } = request;
        let tools =
            WorkspaceTools::new(workspace).map_err(|_| DeepRecallProviderError::InvalidOutput)?;
        Ok(Self {
            messages: vec![
                json!({"role":"system","content":config.prompt}),
                json!({"role":"user","content":query}),
            ],
            tools,
            usage: DeepRecallUsage::default(),
            outstanding: None,
            generation_ids: Vec::new(),
            observed_provider: None,
            observed_model: None,
            malformed_responses: 0,
        })
    }

    fn begin_dispatch(&mut self, reservation: Reservation) {
        self.outstanding = Some(OutstandingRequest {
            reservation,
            generation_id: None,
        });
    }

    fn clear_current_dispatch(&mut self) {
        self.outstanding = None;
    }

    fn attach_generation(&mut self, id: &str) -> Result<(), DeepRecallProviderError> {
        if id.trim().is_empty() || self.outstanding.is_none() {
            return Err(DeepRecallProviderError::InvalidOutput);
        }
        if self.generation_ids.iter().any(|existing| existing == id) {
            return Err(DeepRecallProviderError::InvalidOutput);
        }
        self.outstanding
            .as_mut()
            .expect("checked current dispatch")
            .generation_id = Some(id.to_string());
        self.generation_ids.push(id.to_string());
        Ok(())
    }

    fn has_prior_work(&self) -> bool {
        !self.generation_ids.is_empty()
            || self.usage.context_tokens != 0
            || self.usage.spend_micros != 0
            || !self.tools.checkpoint.is_empty()
    }

    fn observe_route(
        &mut self,
        turn: &ParsedTurn,
        allowed_providers: &[String],
        expected_response_model: &str,
    ) -> Result<(), DeepRecallProviderError> {
        if !allowed_providers
            .iter()
            .any(|allowed| normalize_provider(&turn.provider) == normalize_provider(allowed))
        {
            return Err(DeepRecallProviderError::InvalidOutput);
        }
        if turn.model != expected_response_model {
            return Err(DeepRecallProviderError::InvalidOutput);
        }
        if self
            .observed_provider
            .as_ref()
            .is_some_and(|provider| provider != &turn.provider)
            || self
                .observed_model
                .as_ref()
                .is_some_and(|model| model != &turn.model)
        {
            return Err(DeepRecallProviderError::InvalidOutput);
        }
        self.observed_provider = Some(turn.provider.clone());
        self.observed_model = Some(turn.model.clone());
        Ok(())
    }

    fn settle(
        &mut self,
        prompt_tokens: u64,
        cost_micros: u64,
    ) -> Result<(), DeepRecallProviderError> {
        if self
            .outstanding
            .as_ref()
            .and_then(|outstanding| outstanding.generation_id.as_ref())
            .is_none()
        {
            return Err(DeepRecallProviderError::InvalidOutput);
        }
        let context_tokens = self
            .usage
            .context_tokens
            .checked_add(prompt_tokens)
            .ok_or(DeepRecallProviderError::InvalidOutput)?;
        let spend_micros = self
            .usage
            .spend_micros
            .checked_add(cost_micros)
            .ok_or(DeepRecallProviderError::InvalidOutput)?;
        self.usage.context_tokens = context_tokens;
        self.usage.spend_micros = spend_micros;
        self.outstanding = None;
        Ok(())
    }

    fn stop_reason(&self, limits: &DeepRecallLimits) -> Option<DeepRecallStopReason> {
        if self.usage.spend_micros >= limits.max_spend_micros {
            Some(DeepRecallStopReason::Spend)
        } else if self.usage.context_tokens >= limits.max_context_tokens {
            Some(DeepRecallStopReason::ContextTokens)
        } else if self.usage.tool_iterations >= limits.max_tool_iterations {
            Some(DeepRecallStopReason::ToolIterations)
        } else {
            None
        }
    }

    fn result(
        &self,
        status: DeepRecallStatus,
        stop_reason: DeepRecallStopReason,
        finish: Option<FinishOutcome>,
    ) -> DeepRecallProviderResult {
        let outstanding = self.outstanding.as_ref().map_or(
            Reservation {
                context_tokens: 0,
                spend_micros: 0,
            },
            |outstanding| outstanding.reservation,
        );
        let mut usage = self.usage;
        usage.unsettled_context_tokens_upper_bound = outstanding.context_tokens;
        usage.unsettled_spend_micros_upper_bound = outstanding.spend_micros;
        let source_ids = finish.as_ref().map_or_else(
            || self.tools.checkpoint.clone(),
            |finish| finish.source_ids.clone(),
        );
        let evidence = finish.map_or_else(
            || {
                let status = EvidenceStatus::Insufficient;
                EvidenceDisposition {
                    contract_revision: EVIDENCE_DISPOSITION_CONTRACT_REVISION.to_string(),
                    status,
                    answer_policy: status.answer_policy(),
                    reason: format!("deep recall ended before a valid finish: {stop_reason:?}"),
                }
            },
            |finish| finish.evidence,
        );
        DeepRecallProviderResult {
            status,
            stop_reason,
            source_ids,
            usage,
            generation_ids: self.generation_ids.clone(),
            observed_provider: self.observed_provider.clone(),
            observed_model: self.observed_model.clone(),
            evidence,
        }
    }
}

struct ParsedToolCall {
    call_id: String,
    tool_name: String,
    arguments: String,
}

struct ParsedTurn {
    tool_calls: Vec<ParsedToolCall>,
    reasoning_details: Vec<Value>,
    provider: String,
    model: String,
    prompt_tokens: u64,
    cost_micros: u64,
}

async fn parse_turn(body: ByteStream) -> Result<ParsedTurn, DeepRecallStopReason> {
    let mut events = body.eventsource();
    let mut done = false;
    let mut tool_calls: BTreeMap<u64, ParsedToolCall> = BTreeMap::new();
    let mut reasoning_details = Vec::new();
    let mut provider: Option<String> = None;
    let mut model: Option<String> = None;
    let mut usage: Option<(u64, u64)> = None;

    while let Some(event) = events.next().await {
        let event = event.map_err(|_| DeepRecallStopReason::ProviderError)?;
        if event.data == "[DONE]" {
            done = true;
            break;
        }
        let value: Value =
            serde_json::from_str(&event.data).map_err(|_| DeepRecallStopReason::InvalidOutput)?;
        if value.get("error").is_some() {
            return Err(DeepRecallStopReason::ProviderError);
        }
        if let Some(route) = value.get("provider").and_then(Value::as_str) {
            if provider.as_deref().is_some_and(|seen| seen != route) {
                return Err(DeepRecallStopReason::InvalidOutput);
            }
            provider = Some(route.to_string());
        }
        if let Some(route) = value.get("model").and_then(Value::as_str) {
            if model.as_deref().is_some_and(|seen| seen != route) {
                return Err(DeepRecallStopReason::InvalidOutput);
            }
            model = Some(route.to_string());
        }
        if let Some(raw_usage) = value.get("usage") {
            let prompt_tokens = raw_usage
                .get("prompt_tokens")
                .and_then(Value::as_u64)
                .ok_or(DeepRecallStopReason::InvalidOutput)?;
            let cost_micros = raw_usage
                .get("cost")
                .and_then(cost_value_micros)
                .ok_or(DeepRecallStopReason::InvalidOutput)?;
            if usage.replace((prompt_tokens, cost_micros)).is_some() {
                return Err(DeepRecallStopReason::InvalidOutput);
            }
        }
        let Some(choices) = value.get("choices").and_then(Value::as_array) else {
            if value.get("usage").is_some() {
                continue;
            }
            return Err(DeepRecallStopReason::InvalidOutput);
        };
        if choices.is_empty() && value.get("usage").is_some() {
            continue;
        }
        if choices.len() != 1 || choices[0].get("index").and_then(Value::as_u64) != Some(0) {
            return Err(DeepRecallStopReason::InvalidOutput);
        }
        let delta = choices[0]
            .get("delta")
            .and_then(Value::as_object)
            .ok_or(DeepRecallStopReason::InvalidOutput)?;
        if let Some(details) = delta.get("reasoning_details").and_then(Value::as_array) {
            reasoning_details.extend(details.iter().cloned());
        }
        if let Some(calls) = delta.get("tool_calls").and_then(Value::as_array) {
            for call in calls {
                let index = call
                    .get("index")
                    .and_then(Value::as_u64)
                    .ok_or(DeepRecallStopReason::InvalidOutput)?;
                let accumulated = tool_calls.entry(index).or_insert_with(|| ParsedToolCall {
                    call_id: String::new(),
                    tool_name: String::new(),
                    arguments: String::new(),
                });
                if let Some(id) = call.get("id").and_then(Value::as_str) {
                    if !accumulated.call_id.is_empty() && accumulated.call_id != id {
                        return Err(DeepRecallStopReason::InvalidOutput);
                    }
                    accumulated.call_id = id.to_string();
                }
                if let Some(name) = call.pointer("/function/name").and_then(Value::as_str) {
                    if !accumulated.tool_name.is_empty() && accumulated.tool_name != name {
                        return Err(DeepRecallStopReason::InvalidOutput);
                    }
                    accumulated.tool_name = name.to_string();
                }
                if let Some(fragment) = call.pointer("/function/arguments").and_then(Value::as_str)
                {
                    accumulated.arguments.push_str(fragment);
                }
            }
        }
    }
    let (prompt_tokens, cost_micros) = usage.ok_or(DeepRecallStopReason::InvalidOutput)?;
    if !done || tool_calls.is_empty() {
        return Err(DeepRecallStopReason::InvalidOutput);
    }
    let tool_calls = tool_calls
        .into_iter()
        .enumerate()
        .map(|(expected, (index, call))| {
            if u64::try_from(expected).ok() != Some(index)
                || call.call_id.trim().is_empty()
                || call.tool_name.trim().is_empty()
                || call.arguments.is_empty()
            {
                return Err(DeepRecallStopReason::InvalidOutput);
            }
            Ok(call)
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(ParsedTurn {
        tool_calls,
        reasoning_details,
        provider: provider
            .filter(|value| !value.trim().is_empty())
            .ok_or(DeepRecallStopReason::InvalidOutput)?,
        model: model
            .filter(|value| !value.trim().is_empty())
            .ok_or(DeepRecallStopReason::InvalidOutput)?,
        prompt_tokens,
        cost_micros,
    })
}

fn reservation_for(body: &Value, config: &DeepConfig) -> Option<Reservation> {
    let request_tokens = u64::try_from(serde_json::to_vec(body).ok()?.len()).ok()?;
    let context_tokens = request_tokens.checked_add(config.max_completion_tokens)?;
    let input = reserve_micros(request_tokens, config.input_price_micros_per_million)?;
    let output = reserve_micros(
        config.max_completion_tokens,
        config.output_price_micros_per_million,
    )?;
    Some(Reservation {
        context_tokens,
        spend_micros: input.checked_add(output)?,
    })
}

fn reserve_micros(tokens: u64, price_micros_per_million: u64) -> Option<u64> {
    tokens
        .checked_mul(price_micros_per_million)?
        .checked_add(999_999)?
        .checked_div(1_000_000)
}

fn cost_value_micros(value: &Value) -> Option<u64> {
    let text = value.as_number()?.to_string();
    decimal_dollars_to_micros_ceil(&text)
}

fn decimal_dollars_to_micros_ceil(text: &str) -> Option<u64> {
    if text.starts_with('-') {
        return None;
    }
    let mut exponent_parts = text.split(['e', 'E']);
    let mantissa = exponent_parts.next()?;
    let exponent = exponent_parts
        .next()
        .map_or(Some(0), |value| value.parse::<i64>().ok())?;
    if exponent_parts.next().is_some() {
        return None;
    }
    let mut mantissa_parts = mantissa.split('.');
    let whole = mantissa_parts.next()?;
    let fraction = mantissa_parts.next().unwrap_or("");
    if mantissa_parts.next().is_some()
        || whole.is_empty()
        || !whole.bytes().all(|byte| byte.is_ascii_digit())
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    let digits = format!("{whole}{fraction}");
    let significant = digits.trim_start_matches('0');
    if significant.is_empty() {
        return Some(0);
    }
    let fraction_len = i64::try_from(fraction.len()).ok()?;
    let scale = exponent.checked_add(6)?.checked_sub(fraction_len)?;
    if scale >= 0 {
        let value = significant.parse::<u64>().ok()?;
        let power = u32::try_from(scale).ok()?;
        return value.checked_mul(10_u64.checked_pow(power)?);
    }
    let discarded = usize::try_from(scale.checked_neg()?).ok()?;
    if discarded >= significant.len() {
        return Some(1);
    }
    let split = significant.len() - discarded;
    let integral = significant[..split].parse::<u64>().ok()?;
    let round_up = significant[split..].bytes().any(|byte| byte != b'0') as u64;
    integral.checked_add(round_up)
}

fn normalize_provider(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn elapsed_millis(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

pub fn build_deep_recall_provider_from_config(
    config: DeepConfig,
) -> Result<Arc<dyn DeepRecallProvider>, String> {
    Ok(Arc::new(OpenRouterDeepRecall::new(config)?))
}

pub fn build_deep_recall_provider() -> Result<Option<Arc<dyn DeepRecallProvider>>, String> {
    match deep_mode_from_value(std::env::var("MEMPHANT_DEEP").ok().as_deref())? {
        false => Ok(None),
        true => {
            let required = |name: &str| {
                std::env::var(name)
                    .ok()
                    .filter(|value| !value.trim().is_empty())
                    .ok_or_else(|| format!("{name} is required when MEMPHANT_DEEP=on"))
            };
            let api_key = required("OPENROUTER_API_KEY")?;
            let model = required("MEMPHANT_DEEP_MODEL")?;
            let response_model = required("MEMPHANT_DEEP_RESPONSE_MODEL")?;
            let providers = required("MEMPHANT_DEEP_PROVIDERS")?
                .split(',')
                .map(str::to_string)
                .collect();
            let prompt_path = required("MEMPHANT_DEEP_PROMPT_PATH")?;
            let prompt = std::fs::read_to_string(&prompt_path).map_err(|error| {
                format!("failed to read MEMPHANT_DEEP_PROMPT_PATH={prompt_path}: {error}")
            })?;
            let input_price = parse_price_env("MEMPHANT_DEEP_INPUT_PRICE_MICROS_PER_MILLION")?;
            let output_price = parse_price_env("MEMPHANT_DEEP_OUTPUT_PRICE_MICROS_PER_MILLION")?;
            let config = DeepConfig::new(
                api_key,
                model,
                response_model,
                prompt.trim_end_matches(['\r', '\n']).to_string(),
                providers,
                input_price,
                output_price,
            )?;
            let config = match std::env::var("MEMPHANT_DEEP_OPENROUTER_BASE_URL") {
                Ok(base_url) => config.with_openrouter_base_url(&base_url)?,
                Err(std::env::VarError::NotPresent) => config,
                Err(error) => {
                    return Err(format!(
                        "MEMPHANT_DEEP_OPENROUTER_BASE_URL is not valid UTF-8: {error}"
                    ));
                }
            };
            Ok(Some(build_deep_recall_provider_from_config(config)?))
        }
    }
}

fn deep_mode_from_value(value: Option<&str>) -> Result<bool, String> {
    match value {
        None | Some("off") => Ok(false),
        Some("on") => Ok(true),
        Some(value) => Err(format!(
            "MEMPHANT_DEEP must be exactly `on` or `off`, got {value:?}"
        )),
    }
}

fn parse_price_env(name: &str) -> Result<u64, String> {
    let value =
        std::env::var(name).map_err(|_| format!("{name} is required when MEMPHANT_DEEP=on"))?;
    value
        .parse::<u64>()
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("{name} must be a positive integer"))
}

struct ToolOutcome {
    content: Value,
    finish: Option<FinishOutcome>,
}

#[derive(Debug)]
struct FinishOutcome {
    source_ids: Vec<Uuid>,
    evidence: EvidenceDisposition,
}

struct WorkspaceTools {
    files: BTreeMap<String, String>,
    source_ids: HashSet<Uuid>,
    checkpoint: Vec<Uuid>,
}

impl WorkspaceTools {
    fn new(workspace: DeepWorkspace) -> Result<Self, String> {
        let mut files = BTreeMap::new();
        for file in workspace.files {
            if files.insert(file.path, file.body).is_some() {
                return Err("duplicate workspace path".to_string());
            }
        }
        let manifest = files
            .get("manifest.jsonl")
            .ok_or_else(|| "missing manifest.jsonl".to_string())?;
        let mut source_ids = HashSet::new();
        for line in manifest.lines() {
            let value: Value =
                serde_json::from_str(line).map_err(|_| "invalid manifest.jsonl".to_string())?;
            let id = value
                .get("source_id")
                .and_then(Value::as_str)
                .and_then(|id| Uuid::parse_str(id).ok())
                .ok_or_else(|| "invalid manifest source_id".to_string())?;
            if !source_ids.insert(id) {
                return Err("duplicate manifest source_id".to_string());
            }
        }
        Ok(Self {
            files,
            source_ids,
            checkpoint: Vec::new(),
        })
    }

    fn call(&mut self, name: &str, args: Value) -> ToolOutcome {
        match name {
            "list_files" => self.list_files(args),
            "search_files" => self.search_files(args),
            "read_file" => self.read_file(args),
            "record_evidence" => self.record_evidence(args, false),
            "finish" => self.record_evidence(args, true),
            _ => tool_error("unknown_tool"),
        }
    }

    fn list_files(&self, args: Value) -> ToolOutcome {
        let Some(object) = args.as_object() else {
            return tool_error("invalid_arguments");
        };
        let prefix = match object.get("prefix") {
            None | Some(Value::Null) => "",
            Some(Value::String(prefix)) if prefix.len() <= MAX_QUERY_CHARS => prefix,
            _ => return tool_error("invalid_arguments"),
        };
        let mut files = self
            .files
            .iter()
            .filter(|(path, _)| path.starts_with(prefix))
            .take(MAX_LIST_RESULTS + 1)
            .map(|(path, body)| {
                json!({
                    "path": path,
                    "bytes": body.len(),
                    "lines": body.lines().count(),
                })
            })
            .collect::<Vec<_>>();
        let mut truncated = files.len() > MAX_LIST_RESULTS;
        files.truncate(MAX_LIST_RESULTS);
        while json!({"files": &files, "truncated": truncated})
            .to_string()
            .len()
            > MAX_TOOL_OUTPUT_BYTES
        {
            files.pop();
            truncated = true;
        }
        ToolOutcome {
            content: json!({"files": files, "truncated": truncated}),
            finish: None,
        }
    }

    fn search_files(&self, args: Value) -> ToolOutcome {
        let Some(object) = args.as_object() else {
            return tool_error("invalid_arguments");
        };
        let Some(query) = object.get("query").and_then(Value::as_str) else {
            return tool_error("invalid_arguments");
        };
        if query.is_empty() || query.chars().count() > MAX_QUERY_CHARS {
            return tool_error("invalid_arguments");
        }
        let prefix = match object.get("path_prefix") {
            None | Some(Value::Null) => "",
            Some(Value::String(prefix)) if prefix.len() <= MAX_QUERY_CHARS => prefix,
            _ => return tool_error("invalid_arguments"),
        };
        let needle = query.to_lowercase();
        let mut hits = Vec::new();
        let mut truncated = false;
        'files: for (path, body) in self
            .files
            .iter()
            .filter(|(path, _)| path.starts_with(prefix))
        {
            for (line_index, line) in body.lines().enumerate() {
                if line.to_lowercase().contains(&needle) {
                    if hits.len() == MAX_SEARCH_HITS {
                        truncated = true;
                        break 'files;
                    }
                    let mut text = truncate_utf8(line, MAX_TOOL_OUTPUT_BYTES).to_string();
                    let line_was_truncated = text.len() < line.len();
                    loop {
                        let hit = json!({"path": path, "line": line_index + 1, "text": &text});
                        let mut candidate = hits.clone();
                        candidate.push(hit.clone());
                        if json!({"hits": &candidate, "truncated": truncated || line_was_truncated})
                            .to_string()
                            .len()
                            <= MAX_TOOL_OUTPUT_BYTES
                        {
                            hits.push(hit);
                            truncated |= line_was_truncated;
                            break;
                        }
                        if text.is_empty() {
                            truncated = true;
                            break 'files;
                        }
                        let excess = json!({"hits": &candidate, "truncated": true})
                            .to_string()
                            .len()
                            .saturating_sub(MAX_TOOL_OUTPUT_BYTES)
                            .max(1);
                        let target = text.len().saturating_sub(excess);
                        text.truncate(previous_char_boundary(&text, target));
                        truncated = true;
                    }
                }
            }
        }
        ToolOutcome {
            content: json!({"hits": hits, "truncated": truncated}),
            finish: None,
        }
    }

    fn read_file(&self, args: Value) -> ToolOutcome {
        let Some(object) = args.as_object() else {
            return tool_error("invalid_arguments");
        };
        let Some(path) = object.get("path").and_then(Value::as_str) else {
            return tool_error("invalid_arguments");
        };
        let Some(body) = self.files.get(path) else {
            return tool_error("unknown_path");
        };
        let Some(start) = object.get("start_line").and_then(Value::as_u64) else {
            return tool_error("invalid_arguments");
        };
        let Some(end) = object.get("end_line").and_then(Value::as_u64) else {
            return tool_error("invalid_arguments");
        };
        let Ok(start) = usize::try_from(start) else {
            return tool_error("invalid_range");
        };
        let Ok(end) = usize::try_from(end) else {
            return tool_error("invalid_range");
        };
        if start == 0 || end < start || end - start + 1 > MAX_READ_LINES {
            return tool_error("invalid_range");
        }
        let lines = body.lines().collect::<Vec<_>>();
        // Only `start` past EOF is an error (nothing to read). A model naturally
        // asks for a generous `end_line` (e.g. 1-50) to read a whole file; clamp
        // `end` to the file length and return what exists, like `head -N` on a
        // short file. Erroring here instead tripped the malformed-response limit
        // and aborted every live Deep recall over short (1-line) episode bodies.
        if start > lines.len() {
            return tool_error("invalid_range");
        }
        let end = end.min(lines.len());
        let requested_text = lines[start - 1..end].join("\n");
        let mut text = truncate_utf8(&requested_text, MAX_TOOL_OUTPUT_BYTES).to_string();
        let mut truncated = text.len() < requested_text.len();
        while json!({"path": path, "start_line": start, "end_line": end, "text": text, "truncated": truncated})
            .to_string()
            .len()
            > MAX_TOOL_OUTPUT_BYTES
        {
            let content_len = json!({"path": path, "start_line": start, "end_line": end, "text": text, "truncated": true})
                .to_string()
                .len();
            let target = text
                .len()
                .saturating_sub(content_len.saturating_sub(MAX_TOOL_OUTPUT_BYTES).max(1));
            text.truncate(previous_char_boundary(&text, target));
            truncated = true;
        }
        ToolOutcome {
            content: json!({"path": path, "start_line": start, "end_line": end, "text": text, "truncated": truncated}),
            finish: None,
        }
    }

    fn record_evidence(&mut self, args: Value, finish: bool) -> ToolOutcome {
        let Some(raw_ids) = args.get("source_ids").and_then(Value::as_array) else {
            return tool_error("invalid_arguments");
        };
        if raw_ids.len() > MAX_EVIDENCE_IDS {
            return tool_error("invalid_arguments");
        }
        let mut ids = Vec::new();
        let mut seen = HashSet::new();
        for value in raw_ids {
            let Some(id) = value.as_str().and_then(|id| Uuid::parse_str(id).ok()) else {
                return tool_error("invalid_arguments");
            };
            if !self.source_ids.contains(&id) {
                return tool_error("unknown_source_id");
            }
            if seen.insert(id) {
                ids.push(id);
            }
        }
        if !finish {
            if self
                .checkpoint
                .iter()
                .chain(&ids)
                .copied()
                .collect::<HashSet<_>>()
                .len()
                > MAX_EVIDENCE_IDS
            {
                return tool_error("invalid_arguments");
            }
            for id in &ids {
                if !self.checkpoint.contains(id) {
                    self.checkpoint.push(*id);
                }
            }
        }
        let disposition = if finish {
            let Some(status) = args.get("evidence_status").and_then(Value::as_str) else {
                return tool_error("invalid_arguments");
            };
            let status = match status {
                "supported" => EvidenceStatus::Supported,
                "contradicts-premise" => EvidenceStatus::ContradictsPremise,
                "near-match" => EvidenceStatus::NearMatch,
                "insufficient" => EvidenceStatus::Insufficient,
                _ => return tool_error("invalid_arguments"),
            };
            let Some(reason) = args.get("reason").and_then(Value::as_str) else {
                return tool_error("invalid_arguments");
            };
            let reason = reason.trim();
            if reason.is_empty() || reason.chars().count() > MAX_QUERY_CHARS {
                return tool_error("invalid_arguments");
            }
            if matches!(
                status,
                EvidenceStatus::Supported
                    | EvidenceStatus::ContradictsPremise
                    | EvidenceStatus::NearMatch
            ) && ids.is_empty()
            {
                return tool_error("missing_evidence");
            }
            Some(EvidenceDisposition {
                contract_revision: EVIDENCE_DISPOSITION_CONTRACT_REVISION.to_string(),
                status,
                answer_policy: status.answer_policy(),
                reason: reason.to_string(),
            })
        } else {
            None
        };
        ToolOutcome {
            content: json!({"source_ids": if finish { &ids } else { &self.checkpoint }}),
            finish: disposition.map(|evidence| FinishOutcome {
                source_ids: ids,
                evidence,
            }),
        }
    }
}

fn previous_char_boundary(value: &str, mut boundary: usize) -> usize {
    boundary = boundary.min(value.len());
    while !value.is_char_boundary(boundary) {
        boundary -= 1;
    }
    boundary
}

fn truncate_utf8(value: &str, max_bytes: usize) -> &str {
    &value[..previous_char_boundary(value, max_bytes)]
}

fn tool_error(code: &str) -> ToolOutcome {
    ToolOutcome {
        content: json!({"error": code}),
        finish: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use memphant_types::{DeepWorkspace, DeepWorkspaceFile};
    use serde_json::json;
    use std::collections::VecDeque;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Arc;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::task::{Context, Poll};
    use std::time::Instant;
    use uuid::Uuid;

    struct PanicTransport;

    fn bounded_http_server(
        listener: TcpListener,
        response: Option<String>,
    ) -> (Arc<AtomicUsize>, std::thread::JoinHandle<()>) {
        listener.set_nonblocking(true).unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let observed = calls.clone();
        let server = std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_millis(750);
            while Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut socket, _)) => {
                        observed.fetch_add(1, Ordering::SeqCst);
                        socket.set_nonblocking(false).unwrap();
                        socket
                            .set_read_timeout(Some(Duration::from_millis(200)))
                            .unwrap();
                        let mut buffer = [0u8; 8192];
                        let _ = socket.read(&mut buffer);
                        if let Some(response) = &response {
                            let _ = socket.write_all(response.as_bytes());
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("test server accept failed: {error}"),
                }
            }
        });
        (calls, server)
    }

    impl Transport for PanicTransport {
        fn post<'a>(
            &'a self,
            _: &'a Value,
        ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>
        {
            Box::pin(async { panic!("request was not expected") })
        }

        fn generation<'a>(
            &'a self,
            _: &'a str,
        ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>
        {
            Box::pin(async { panic!("settlement was not expected") })
        }
    }

    struct ScriptTransport {
        responses: Mutex<VecDeque<Result<TransportResponse, TransportError>>>,
        requests: Mutex<Vec<Value>>,
        generation_requests: Mutex<Vec<String>>,
    }

    impl ScriptTransport {
        fn new(responses: Vec<TransportResponse>) -> Self {
            Self {
                responses: Mutex::new(responses.into_iter().map(Ok).collect()),
                requests: Mutex::new(Vec::new()),
                generation_requests: Mutex::new(Vec::new()),
            }
        }

        fn with_results(responses: Vec<Result<TransportResponse, TransportError>>) -> Self {
            Self {
                responses: Mutex::new(responses.into()),
                requests: Mutex::new(Vec::new()),
                generation_requests: Mutex::new(Vec::new()),
            }
        }
    }

    impl Transport for ScriptTransport {
        fn post<'a>(
            &'a self,
            body: &'a Value,
        ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>
        {
            self.requests.lock().unwrap().push(body.clone());
            let response = self.responses.lock().unwrap().pop_front().unwrap();
            Box::pin(async move { response })
        }

        fn generation<'a>(
            &'a self,
            id: &'a str,
        ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>
        {
            self.generation_requests
                .lock()
                .unwrap()
                .push(id.to_string());
            Box::pin(async { Err(TransportError) })
        }
    }

    fn sse_response(id: &str, events: Vec<Value>) -> TransportResponse {
        let mut bytes = Vec::new();
        for event in events {
            bytes.extend_from_slice(format!("data: {event}\n\n").as_bytes());
        }
        bytes.extend_from_slice(b": keepalive\n\ndata: [DONE]\n\n");
        let split = bytes.len() / 2;
        let chunks = vec![bytes[..split].to_vec(), bytes[split..].to_vec()];
        TransportResponse {
            status: 200,
            generation_id: Some(id.to_string()),
            retry_after: None,
            content_type: Some("text/event-stream; charset=utf-8".into()),
            body: Box::pin(futures::stream::iter(chunks.into_iter().map(Ok))),
        }
    }

    fn evidence_response(id: &str, call_id: &str, source_id: Uuid) -> TransportResponse {
        sse_response(
            id,
            vec![json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":call_id,"function":{"name":"record_evidence","arguments":format!("{{\"source_ids\":[\"{source_id}\"]}}")}}]}}],
                "usage":{"prompt_tokens":10,"completion_tokens":1,"cost":0.00001}
            })],
        )
    }

    fn terminal_response(status: u16) -> TransportResponse {
        TransportResponse {
            status,
            generation_id: None,
            retry_after: Some(Duration::ZERO),
            content_type: Some("application/json".into()),
            body: Box::pin(futures::stream::empty()),
        }
    }

    struct DropSignal(Arc<AtomicBool>);

    impl Drop for DropSignal {
        fn drop(&mut self) {
            self.0.store(true, Ordering::SeqCst);
        }
    }

    struct PendingPostTransport(Arc<AtomicBool>);

    impl Transport for PendingPostTransport {
        fn post<'a>(
            &'a self,
            _: &'a Value,
        ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>
        {
            let dropped = self.0.clone();
            Box::pin(async move {
                let _guard = DropSignal(dropped);
                futures::future::pending().await
            })
        }

        fn generation<'a>(
            &'a self,
            _: &'a str,
        ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>
        {
            Box::pin(async { Err(TransportError) })
        }
    }

    struct PendingStream {
        dropped: Arc<AtomicBool>,
    }

    impl Stream for PendingStream {
        type Item = Result<Vec<u8>, TransportError>;

        fn poll_next(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<Option<Self::Item>> {
            Poll::Pending
        }
    }

    impl Drop for PendingStream {
        fn drop(&mut self) {
            self.dropped.store(true, Ordering::SeqCst);
        }
    }

    struct PendingBodyTransport(Arc<AtomicBool>);

    impl Transport for PendingBodyTransport {
        fn post<'a>(
            &'a self,
            _: &'a Value,
        ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>
        {
            let dropped = self.0.clone();
            Box::pin(async move {
                Ok(TransportResponse {
                    status: 200,
                    generation_id: Some("gen-pending".into()),
                    retry_after: None,
                    content_type: Some("text/event-stream".into()),
                    body: Box::pin(PendingStream { dropped }),
                })
            })
        }

        fn generation<'a>(
            &'a self,
            _: &'a str,
        ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>
        {
            Box::pin(async { Err(TransportError) })
        }
    }

    struct PendingSettlementTransport;

    impl Transport for PendingSettlementTransport {
        fn post<'a>(
            &'a self,
            _: &'a Value,
        ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>
        {
            Box::pin(async {
                Ok(TransportResponse {
                    status: 502,
                    generation_id: Some("gen-early-failure".into()),
                    retry_after: None,
                    content_type: Some("application/json".into()),
                    body: Box::pin(futures::stream::empty()),
                })
            })
        }

        fn generation<'a>(
            &'a self,
            _: &'a str,
        ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>
        {
            Box::pin(futures::future::pending())
        }
    }

    struct CountingErrorTransport(AtomicUsize);

    impl Transport for CountingErrorTransport {
        fn post<'a>(
            &'a self,
            _: &'a Value,
        ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>
        {
            self.0.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { Err(TransportError) })
        }

        fn generation<'a>(
            &'a self,
            _: &'a str,
        ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>
        {
            Box::pin(async { Err(TransportError) })
        }
    }

    struct SettledFailureTransport;

    impl Transport for SettledFailureTransport {
        fn post<'a>(
            &'a self,
            _: &'a Value,
        ) -> Pin<Box<dyn Future<Output = Result<TransportResponse, TransportError>> + Send + 'a>>
        {
            Box::pin(async {
                Ok(TransportResponse {
                    status: 502,
                    generation_id: Some("gen-settled".into()),
                    retry_after: None,
                    content_type: Some("application/json".into()),
                    body: Box::pin(futures::stream::empty()),
                })
            })
        }

        fn generation<'a>(
            &'a self,
            _: &'a str,
        ) -> Pin<Box<dyn Future<Output = Result<GenerationUsage, TransportError>> + Send + 'a>>
        {
            Box::pin(async {
                Ok(GenerationUsage {
                    prompt_tokens: 77,
                    cost_micros: 88,
                })
            })
        }
    }

    fn workspace() -> (DeepWorkspace, Uuid, Uuid) {
        let a = Uuid::from_u128(1);
        let b = Uuid::from_u128(2);
        let manifest = format!("{{\"source_id\":\"{a}\"}}\n{{\"source_id\":\"{b}\"}}\n");
        (
            DeepWorkspace {
                files: vec![
                    DeepWorkspaceFile {
                        path: "manifest.jsonl".into(),
                        body: manifest,
                    },
                    DeepWorkspaceFile {
                        path: format!("episodes/{a}.md"),
                        body: "Alpha βeta\nsecond ALPHA line\n".into(),
                    },
                    DeepWorkspaceFile {
                        path: format!("resources/{b}.md"),
                        body: "zeta\n".into(),
                    },
                ],
                manifest_sha256: "manifest".into(),
                workspace_sha256: "workspace".into(),
            },
            a,
            b,
        )
    }

    #[test]
    fn in_memory_tools_are_bounded_literal_and_finish_ordered() {
        let (workspace, a, b) = workspace();
        let mut tools = WorkspaceTools::new(workspace).unwrap();

        let listed = tools.call("list_files", json!({"prefix": "episodes/"}));
        assert_eq!(
            listed.content["files"][0]["path"],
            format!("episodes/{a}.md")
        );
        assert_eq!(
            listed.content["files"][0]["bytes"],
            "Alpha βeta\nsecond ALPHA line\n".len()
        );
        assert_eq!(listed.content["files"][0]["lines"], 2);
        let searched = tools.call("search_files", json!({"query": "alpha"}));
        assert_eq!(searched.content["hits"][0]["line"], 1);
        assert_eq!(searched.content["hits"][1]["line"], 2);
        let read = tools.call(
            "read_file",
            json!({"path": format!("episodes/{a}.md"), "start_line": 1, "end_line": 1}),
        );
        assert_eq!(read.content["text"], "Alpha βeta");
        assert_eq!(
            tools
                .call("record_evidence", json!({"source_ids": [a, a, b]}))
                .content["source_ids"],
            json!([a, b])
        );
        let finished = tools.call(
            "finish",
            json!({
                "source_ids": [b, a, b],
                "evidence_status": "supported",
                "reason": "both sources directly support the answer"
            }),
        );
        let finished = finished.finish.expect("valid finish");
        assert_eq!(finished.source_ids, vec![b, a]);
        assert_eq!(finished.evidence.status, EvidenceStatus::Supported);
    }

    #[test]
    fn finish_rejects_unknown_or_uncalibrated_evidence_statuses() {
        let (workspace, source_id, _) = workspace();
        let mut tools = WorkspaceTools::new(workspace).unwrap();
        for args in [
            json!({"source_ids": [source_id]}),
            json!({
                "source_ids": [source_id],
                "evidence_status": "probably",
                "reason": "guess"
            }),
            json!({
                "source_ids": [],
                "evidence_status": "supported",
                "reason": "unsupported confidence"
            }),
            json!({
                "source_ids": [source_id],
                "evidence_status": "contradicts-premise",
                "reason": "   "
            }),
        ] {
            let outcome = tools.call("finish", args);
            assert!(outcome.finish.is_none());
            assert!(outcome.content.get("error").is_some());
        }
    }

    #[test]
    fn read_file_clamps_end_line_past_eof_instead_of_erroring() {
        // P0.3 live-Deep root cause: episode bodies are often 1 line, and a
        // model naturally reads a generous range (`end_line: 50`) to see the
        // whole file. The tool must clamp `end` to the file length and return
        // the available lines (like `head -50` on a short file), NOT reject the
        // read as `invalid_range` — two such rejections trip the malformed-
        // response limit and abort Deep with empty evidence.
        let (workspace, _a, b) = workspace();
        let mut tools = WorkspaceTools::new(workspace).unwrap();
        let read = tools.call(
            "read_file",
            json!({"path": format!("resources/{b}.md"), "start_line": 1, "end_line": 50}),
        );
        assert!(
            read.content.get("error").is_none(),
            "reading past EOF must not error: {}",
            read.content
        );
        assert_eq!(read.content["text"], "zeta");
        // The response reports the clamped end, so the model sees the true extent.
        assert_eq!(read.content["end_line"], 1);
        // A start_line genuinely past EOF is still an error (nothing to read).
        let past = tools.call(
            "read_file",
            json!({"path": format!("resources/{b}.md"), "start_line": 5, "end_line": 9}),
        );
        assert_eq!(past.content["error"], "invalid_range");
    }

    #[test]
    fn advertised_tools_are_exact_and_unique() {
        let definitions = tool_definitions();
        let names = definitions
            .as_array()
            .unwrap()
            .iter()
            .map(|definition| definition["function"]["name"].as_str().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(
            names,
            vec![
                "list_files",
                "search_files",
                "read_file",
                "record_evidence",
                "finish"
            ]
        );
        assert_eq!(
            names.iter().copied().collect::<HashSet<_>>().len(),
            names.len()
        );
    }

    #[test]
    fn workspace_rejects_duplicate_paths_while_consuming_owned_files() {
        let (mut workspace, _, _) = workspace();
        workspace.files.push(DeepWorkspaceFile {
            path: workspace.files[0].path.clone(),
            body: "shadow copy".into(),
        });
        assert_eq!(
            WorkspaceTools::new(workspace).err().unwrap(),
            "duplicate workspace path"
        );
    }

    #[test]
    fn serialized_tool_content_never_exceeds_cap() {
        let source_id = Uuid::from_u128(77);
        let huge_line = format!("needle {}", "🦀\"".repeat(MAX_TOOL_OUTPUT_BYTES));
        let huge_path = format!("episodes/{}.md", "p".repeat(MAX_TOOL_OUTPUT_BYTES * 2));
        let workspace = DeepWorkspace {
            files: vec![
                DeepWorkspaceFile {
                    path: "manifest.jsonl".into(),
                    body: format!("{{\"source_id\":\"{source_id}\"}}\n"),
                },
                DeepWorkspaceFile {
                    path: "episodes/huge.md".into(),
                    body: huge_line,
                },
                DeepWorkspaceFile {
                    path: huge_path,
                    body: "needle".into(),
                },
            ],
            manifest_sha256: "manifest".into(),
            workspace_sha256: "workspace".into(),
        };
        let mut tools = WorkspaceTools::new(workspace).unwrap();
        for outcome in [
            tools.call("list_files", json!({"prefix": "episodes/"})),
            tools.call("search_files", json!({"query": "needle"})),
            tools.call(
                "read_file",
                json!({"path": "episodes/huge.md", "start_line": 1, "end_line": 1}),
            ),
        ] {
            assert!(outcome.content.to_string().len() <= MAX_TOOL_OUTPUT_BYTES);
        }
    }

    #[test]
    fn provider_decimal_cost_is_rounded_up_without_floating_point() {
        assert_eq!(decimal_dollars_to_micros_ceil("0.0000100"), Some(10));
        assert_eq!(decimal_dollars_to_micros_ceil("0.0000101"), Some(11));
        assert_eq!(decimal_dollars_to_micros_ceil("1.01e-5"), Some(11));
        assert_eq!(decimal_dollars_to_micros_ceil("1e-100"), Some(1));
        assert_eq!(
            decimal_dollars_to_micros_ceil("18446744073709.551615"),
            Some(u64::MAX)
        );
        assert_eq!(
            decimal_dollars_to_micros_ceil("18446744073709.551616"),
            None
        );
        assert_eq!(decimal_dollars_to_micros_ceil("1e20"), None);
        assert_eq!(decimal_dollars_to_micros_ceil("-0.1"), None);
    }

    #[test]
    fn tools_fail_closed_with_stable_codes() {
        let (workspace, _, _) = workspace();
        let mut tools = WorkspaceTools::new(workspace).unwrap();
        assert_eq!(
            tools.call("shell", json!({})).content["error"],
            "unknown_tool"
        );
        assert_eq!(
            tools
                .call("search_files", json!({"query": "[a-z]+".repeat(100)}))
                .content["error"],
            "invalid_arguments"
        );
        assert_eq!(
            tools
                .call(
                    "read_file",
                    json!({"path": "../secret", "start_line": 1, "end_line": 1})
                )
                .content["error"],
            "unknown_path"
        );
        assert_eq!(
            tools
                .call("finish", json!({"source_ids": [Uuid::from_u128(99)]}))
                .content["error"],
            "unknown_source_id"
        );
    }

    fn config() -> DeepConfig {
        DeepConfig::new(
            "secret".into(),
            "anthropic/claude-sonnet-5".into(),
            "anthropic/claude-sonnet-5".into(),
            "Use tools only.".into(),
            vec!["azure".into()],
            2_000_000,
            10_000_000,
        )
        .unwrap()
    }

    fn qwen_config() -> DeepConfig {
        DeepConfig::new(
            "secret".into(),
            "qwen/qwen3.5-9b-20260310".into(),
            "qwen/qwen3.5-9b".into(),
            "Use tools only.".into(),
            vec!["deepinfra".into()],
            100_000,
            150_000,
        )
        .unwrap()
    }

    #[test]
    fn qwen_campaign_route_is_deepinfra_only_without_fallbacks() {
        let candidate = qwen_config();
        let provider =
            OpenRouterDeepRecall::with_transport(candidate.clone(), Arc::new(PanicTransport));
        let body = provider.request_body(&[json!({"role": "user", "content": "q"})]);
        assert_eq!(body["model"], "qwen/qwen3.5-9b-20260310");
        assert_eq!(body["provider"]["only"], json!(["deepinfra"]));
        assert_eq!(body["provider"]["allow_fallbacks"], false);
        assert_eq!(body["provider"]["quantizations"], json!(["bf16"]));

        let mut state = LoopState::new(
            DeepRecallProviderRequest {
                query: "q".into(),
                workspace: workspace().0,
            },
            &candidate,
        )
        .unwrap();
        let turn = ParsedTurn {
            tool_calls: Vec::new(),
            reasoning_details: Vec::new(),
            provider: "DeepInfra".into(),
            model: "qwen/qwen3.5-9b".into(),
            prompt_tokens: 1,
            cost_micros: 1,
        };
        assert!(
            state
                .observe_route(&turn, &candidate.providers, &candidate.response_model)
                .is_ok()
        );
        assert!(
            state
                .observe_route(
                    &ParsedTurn {
                        provider: "Azure".into(),
                        ..turn
                    },
                    &candidate.providers,
                    &candidate.response_model,
                )
                .is_err()
        );
    }

    #[test]
    fn request_is_exact_model_private_azure_and_locally_price_bounded() {
        let provider = OpenRouterDeepRecall::with_transport(config(), Arc::new(PanicTransport));
        let body = provider.request_body(&[json!({"role": "user", "content": "q"})]);
        assert_eq!(
            body.as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect::<std::collections::BTreeSet<_>>(),
            std::collections::BTreeSet::from([
                "max_completion_tokens",
                "messages",
                "model",
                "provider",
                "stream",
                "tool_choice",
                "tools",
            ]),
            "new top-level request parameters must be proven against every frozen endpoint"
        );
        assert_eq!(body["model"], "anthropic/claude-sonnet-5");
        assert!(body.get("models").is_none());
        assert_eq!(body["stream"], true);
        assert_eq!(body["tool_choice"], "required");
        assert!(
            body.get("parallel_tool_calls").is_none(),
            "Azure does not advertise parallel_tool_calls under require_parameters; \
             bounded provider-default calls execute in stable index order"
        );
        assert_eq!(body["max_completion_tokens"], 4096);
        assert_eq!(body["provider"]["only"], json!(["azure"]));
        assert_eq!(body["provider"]["zdr"], true);
        assert_eq!(body["provider"]["data_collection"], "deny");
        assert_eq!(body["provider"]["require_parameters"], true);
        assert_eq!(
            body["provider"]
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect::<std::collections::BTreeSet<_>>(),
            std::collections::BTreeSet::from([
                "allow_fallbacks",
                "data_collection",
                "max_price",
                "only",
                "require_parameters",
                "zdr",
            ])
        );
        assert_eq!(body["provider"]["max_price"]["prompt"], 2);
        assert_eq!(body["provider"]["max_price"]["completion"], 10);
        assert!(body.get("usage").is_none());
        assert!(body.get("stream_options").is_none());
    }

    #[test]
    fn config_rejects_non_azure_or_floating_model() {
        assert!(
            DeepConfig::new(
                "key".into(),
                "openai/gpt-latest".into(),
                "openai/gpt".into(),
                "prompt".into(),
                vec!["azure".into()],
                1,
                1,
            )
            .is_err()
        );
        assert!(
            DeepConfig::new(
                "key".into(),
                "qwen/qwen3.5-9b-20260310".into(),
                "qwen/qwen3.5-9b".into(),
                "prompt".into(),
                vec!["azure".into()],
                1,
                1,
            )
            .is_err()
        );
        assert!(
            DeepConfig::new(
                "key".into(),
                "openai/gpt-5.6-sol".into(),
                "openai/gpt-5.6-sol".into(),
                "prompt".into(),
                vec!["bedrock".into()],
                1,
                1,
            )
            .is_err()
        );
        assert!(
            config()
                .with_openrouter_base_url("http://example.com/api/v1")
                .is_err()
        );
        assert!(
            config()
                .with_openrouter_base_url("https://user:secret@example.com/api/v1")
                .is_err()
        );
        assert!(
            config()
                .with_openrouter_base_url("http://127.0.0.1:9999/api/v1")
                .is_ok()
        );
    }

    #[test]
    fn exact_request_model_accepts_only_registered_canonical_stream_model() {
        let candidate = DeepConfig::new(
            "secret".into(),
            "anthropic/claude-sonnet-5-20260630".into(),
            "anthropic/claude-sonnet-5".into(),
            "Use tools only.".into(),
            vec!["azure".into()],
            2_000_000,
            10_000_000,
        )
        .unwrap();
        let mut state = LoopState::new(
            DeepRecallProviderRequest {
                query: "q".into(),
                workspace: workspace().0,
            },
            &candidate,
        )
        .unwrap();
        let turn = ParsedTurn {
            tool_calls: vec![ParsedToolCall {
                call_id: "call-1".into(),
                tool_name: "finish".into(),
                arguments: "{\"source_ids\":[]}".into(),
            }],
            reasoning_details: Vec::new(),
            provider: "Azure".into(),
            model: "anthropic/claude-sonnet-5".into(),
            prompt_tokens: 10,
            cost_micros: 20,
        };

        assert!(
            state
                .observe_route(&turn, &candidate.providers, &candidate.response_model)
                .is_ok()
        );
        let wrong_snapshot = ParsedTurn {
            model: "anthropic/claude-sonnet-5-20260501".into(),
            ..turn
        };
        assert!(
            state
                .observe_route(
                    &wrong_snapshot,
                    &candidate.providers,
                    &candidate.response_model,
                )
                .is_err()
        );
    }

    #[tokio::test]
    async fn scripted_agent_preserves_reasoning_usage_generation_ids_and_finish_order() {
        let (workspace, a, b) = workspace();
        let responses = vec![
            sse_response(
                "gen-1",
                vec![json!({
                    "model":"anthropic/claude-sonnet-5",
                    "provider":"Azure",
                    "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"list_files","arguments":"{}"}}]},"finish_reason":"tool_calls"}],
                    "usage":{"prompt_tokens":10,"completion_tokens":2,"cost":0.0001}
                })],
            ),
            sse_response(
                "gen-2",
                vec![json!({
                    "model":"anthropic/claude-sonnet-5",
                    "provider":"Azure",
                    "choices":[{"index":0,"delta":{"reasoning_details":[{"type":"reasoning.text","text":"keep this exact"}],"tool_calls":[{"index":0,"id":"call-2","type":"function","function":{"name":"record_evidence","arguments":format!("{{\"source_ids\":[\"{a}\"]}}")}}]},"finish_reason":"tool_calls"}],
                    "usage":{"prompt_tokens":20,"completion_tokens":3,"cost":0.0002}
                })],
            ),
            sse_response(
                "gen-3",
                vec![json!({
                    "model":"anthropic/claude-sonnet-5",
                    "provider":"Azure",
                    "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-3","type":"function","function":{"name":"finish","arguments":format!("{{\"source_ids\":[\"{b}\",\"{a}\"],\"evidence_status\":\"supported\",\"reason\":\"direct support in both sources\"}}")}}]},"finish_reason":"tool_calls"}],
                    "usage":{"prompt_tokens":30,"completion_tokens":4,"cost":0.0003}
                })],
            ),
        ];
        let transport = Arc::new(ScriptTransport::new(responses));
        let provider = OpenRouterDeepRecall::with_transport(config(), transport.clone());
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "find it".into(),
                workspace,
            })
            .await
            .unwrap();

        assert_eq!(result.status, DeepRecallStatus::Completed);
        assert_eq!(result.source_ids, vec![b, a]);
        assert_eq!(result.generation_ids, vec!["gen-1", "gen-2", "gen-3"]);
        assert_eq!(result.observed_provider.as_deref(), Some("Azure"));
        assert_eq!(result.usage.tool_iterations, 3);
        assert_eq!(result.usage.context_tokens, 60);
        assert_eq!(result.usage.spend_micros, 600);
        assert_eq!(result.usage.unsettled_context_tokens_upper_bound, 0);
        let requests = transport.requests.lock().unwrap();
        assert_eq!(
            requests[2]["messages"]
                .as_array()
                .unwrap()
                .iter()
                .find(|message| {
                    message
                        .get("reasoning_details")
                        .and_then(Value::as_array)
                        .is_some_and(|details| !details.is_empty())
                })
                .unwrap()["reasoning_details"],
            json!([{"type":"reasoning.text","text":"keep this exact"}])
        );
    }

    #[tokio::test]
    async fn parallel_tool_calls_execute_in_index_order_with_one_provider_turn() {
        let (workspace, a, b) = workspace();
        let responses = vec![
            sse_response(
                "gen-parallel",
                vec![json!({
                    "model":"anthropic/claude-sonnet-5",
                    "provider":"Azure",
                    "choices":[{"index":0,"delta":{"tool_calls":[
                        {"index":0,"id":"call-a","type":"function","function":{"name":"record_evidence","arguments":format!("{{\"source_ids\":[\"{a}\"]}}")}},
                        {"index":1,"id":"call-b","type":"function","function":{"name":"record_evidence","arguments":format!("{{\"source_ids\":[\"{b}\"]}}")}}
                    ]},"finish_reason":"tool_calls"}],
                    "usage":{"prompt_tokens":10,"completion_tokens":2,"cost":0.0001}
                })],
            ),
            sse_response(
                "gen-finish",
                vec![json!({
                    "model":"anthropic/claude-sonnet-5",
                    "provider":"Azure",
                    "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-finish","type":"function","function":{"name":"finish","arguments":format!("{{\"source_ids\":[\"{b}\",\"{a}\"],\"evidence_status\":\"supported\",\"reason\":\"direct support in both sources\"}}")}}]},"finish_reason":"tool_calls"}],
                    "usage":{"prompt_tokens":20,"completion_tokens":2,"cost":0.0002}
                })],
            ),
        ];
        let transport = Arc::new(ScriptTransport::new(responses));
        let provider = OpenRouterDeepRecall::with_transport(config(), transport.clone());
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "find both".into(),
                workspace,
            })
            .await
            .unwrap();

        assert_eq!(result.status, DeepRecallStatus::Completed);
        assert_eq!(result.source_ids, vec![b, a]);
        assert_eq!(result.usage.tool_iterations, 3);
        let requests = transport.requests.lock().unwrap();
        assert_eq!(requests.len(), 2);
        let messages = requests[1]["messages"].as_array().unwrap();
        let assistant = messages
            .iter()
            .find(|message| message["role"] == "assistant")
            .unwrap();
        assert_eq!(assistant["tool_calls"].as_array().unwrap().len(), 2);
        assert_eq!(assistant["tool_calls"][0]["id"], "call-a");
        assert_eq!(assistant["tool_calls"][1]["id"], "call-b");
        assert_eq!(
            messages
                .iter()
                .filter(|message| message["role"] == "tool")
                .map(|message| message["tool_call_id"].as_str().unwrap())
                .collect::<Vec<_>>(),
            vec!["call-a", "call-b"]
        );
    }

    #[tokio::test]
    async fn successful_stream_without_generation_id_is_partial_and_unsettled() {
        let (workspace, _, _) = workspace();
        let mut response = sse_response(
            "ignored",
            vec![json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"finish","arguments":"{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"no supporting source found\"}"}}]}}],
                "usage":{"prompt_tokens":1,"completion_tokens":1,"cost":0.000001}
            })],
        );
        response.generation_id = None;
        let provider = OpenRouterDeepRecall::with_transport(
            config(),
            Arc::new(ScriptTransport::new(vec![response])),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::InvalidOutput);
        assert!(result.generation_ids.is_empty());
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
    }

    #[tokio::test]
    async fn routed_model_mismatch_is_partial_and_keeps_paid_reservation_unsettled() {
        let (workspace, _, _) = workspace();
        let response = sse_response(
            "gen-wrong-model",
            vec![json!({
                "model":"anthropic/claude-opus-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"finish","arguments":"{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"no supporting source found\"}"}}]}}],
                "usage":{"prompt_tokens":10,"completion_tokens":1,"cost":0.00001}
            })],
        );
        let provider = OpenRouterDeepRecall::with_transport(
            config(),
            Arc::new(ScriptTransport::new(vec![response])),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();

        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::InvalidOutput);
        assert_eq!(result.generation_ids, vec!["gen-wrong-model"]);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
    }

    #[tokio::test]
    async fn later_missing_or_duplicate_generation_keeps_current_reservation_unsettled() {
        for duplicate in [false, true] {
            let (workspace, source_id, _) = workspace();
            let first = evidence_response("gen-prior", "call-prior", source_id);
            let mut second = sse_response(
                "gen-current",
                vec![json!({
                    "model":"anthropic/claude-sonnet-5","provider":"Azure",
                    "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-current","function":{"name":"finish","arguments":"{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"no supporting source found\"}"}}]}}],
                    "usage":{"prompt_tokens":20,"completion_tokens":1,"cost":0.00002}
                })],
            );
            second.generation_id = duplicate.then(|| "gen-prior".to_string());
            let transport = Arc::new(ScriptTransport::new(vec![first, second]));
            let provider = OpenRouterDeepRecall::with_transport(config(), transport.clone());
            let result = provider
                .gather(DeepRecallProviderRequest {
                    query: "q".into(),
                    workspace,
                })
                .await
                .unwrap();

            assert_eq!(result.status, DeepRecallStatus::Partial);
            assert_eq!(result.stop_reason, DeepRecallStopReason::InvalidOutput);
            assert_eq!(result.source_ids, vec![source_id]);
            assert_eq!(result.generation_ids, vec!["gen-prior"]);
            assert_eq!(result.usage.context_tokens, 10);
            assert_eq!(result.usage.spend_micros, 10);
            assert!(result.usage.unsettled_context_tokens_upper_bound > 0);
            assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
            assert!(transport.generation_requests.lock().unwrap().is_empty());
        }
    }

    #[tokio::test]
    async fn later_pre_header_error_never_resettles_prior_generation() {
        let (workspace, source_id, _) = workspace();
        let transport = Arc::new(ScriptTransport::with_results(vec![
            Ok(evidence_response("gen-prior", "call-prior", source_id)),
            Err(TransportError),
        ]));
        let provider = OpenRouterDeepRecall::with_transport(config(), transport.clone());
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();

        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::ProviderError);
        assert_eq!(result.generation_ids, vec!["gen-prior"]);
        assert_eq!(result.usage.context_tokens, 10);
        assert_eq!(result.usage.spend_micros, 10);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
        assert!(transport.generation_requests.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn later_body_error_settles_only_the_current_generation() {
        let (workspace, source_id, _) = workspace();
        let body_error = TransportResponse {
            status: 200,
            generation_id: Some("gen-current".into()),
            retry_after: None,
            content_type: Some("text/event-stream".into()),
            body: Box::pin(futures::stream::iter([Err(TransportError)])),
        };
        let transport = Arc::new(ScriptTransport::new(vec![
            evidence_response("gen-prior", "call-prior", source_id),
            body_error,
        ]));
        let provider = OpenRouterDeepRecall::with_transport(config(), transport.clone());
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();

        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::ProviderError);
        assert_eq!(result.generation_ids, vec!["gen-prior", "gen-current"]);
        assert_eq!(result.usage.context_tokens, 10);
        assert_eq!(result.usage.spend_micros, 10);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
        assert_eq!(
            *transport.generation_requests.lock().unwrap(),
            vec!["gen-current"]
        );
    }

    #[tokio::test]
    async fn later_terminal_no_id_http_failures_preserve_prior_paid_partial_facts() {
        for status in [400, 401, 429, 500] {
            let (workspace, source_id, _) = workspace();
            let retry_count = if status == 429 || status == 500 { 3 } else { 1 };
            let mut responses = vec![evidence_response("gen-prior", "call-prior", source_id)];
            responses.extend((0..retry_count).map(|_| terminal_response(status)));
            let transport = Arc::new(ScriptTransport::new(responses));
            let provider = OpenRouterDeepRecall::with_transport(config(), transport.clone());
            let result = provider
                .gather(DeepRecallProviderRequest {
                    query: "q".into(),
                    workspace,
                })
                .await
                .unwrap();

            assert_eq!(result.status, DeepRecallStatus::Partial, "status {status}");
            assert_eq!(
                result.stop_reason,
                DeepRecallStopReason::ProviderError,
                "status {status}"
            );
            assert_eq!(result.source_ids, vec![source_id]);
            assert_eq!(result.generation_ids, vec!["gen-prior"]);
            assert_eq!(result.usage.context_tokens, 10);
            assert_eq!(result.usage.spend_micros, 10);
            assert_eq!(result.usage.unsettled_spend_micros_upper_bound, 0);
            assert!(transport.generation_requests.lock().unwrap().is_empty());
        }
    }

    #[tokio::test]
    async fn pristine_terminal_no_id_http_failure_is_unavailable() {
        let (workspace, _, _) = workspace();
        let provider = OpenRouterDeepRecall::with_transport(
            config(),
            Arc::new(ScriptTransport::new(vec![terminal_response(400)])),
        );
        assert!(matches!(
            provider
                .gather(DeepRecallProviderRequest {
                    query: "q".into(),
                    workspace,
                })
                .await,
            Err(DeepRecallProviderError::Unavailable)
        ));
    }

    #[tokio::test]
    async fn accepted_generation_with_wrong_content_type_is_partial_and_unsettled() {
        let (workspace, _, _) = workspace();
        let mut response = sse_response(
            "gen-wrong-content-type",
            vec![json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"finish","arguments":"{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"no supporting source found\"}"}}]}}],
                "usage":{"prompt_tokens":1,"completion_tokens":1,"cost":0.000001}
            })],
        );
        response.content_type = Some("application/json".into());
        let provider = OpenRouterDeepRecall::with_transport(
            config(),
            Arc::new(ScriptTransport::new(vec![response])),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::InvalidOutput);
        assert_eq!(result.generation_ids, vec!["gen-wrong-content-type"]);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
    }

    #[tokio::test]
    async fn caller_drop_cancels_pending_post_future() {
        let (workspace, _, _) = workspace();
        let dropped = Arc::new(AtomicBool::new(false));
        let provider = Arc::new(OpenRouterDeepRecall::with_transport(
            config(),
            Arc::new(PendingPostTransport(dropped.clone())),
        ));
        let task = tokio::spawn(async move {
            provider
                .gather(DeepRecallProviderRequest {
                    query: "q".into(),
                    workspace,
                })
                .await
        });
        tokio::task::yield_now().await;
        task.abort();
        let _ = task.await;
        assert!(dropped.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn wall_deadline_drops_pending_body_stream() {
        let (workspace, _, _) = workspace();
        let dropped = Arc::new(AtomicBool::new(false));
        let provider = OpenRouterDeepRecall::with_transport(
            config().with_test_timing(40, 10),
            Arc::new(PendingBodyTransport(dropped.clone())),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Capped);
        assert_eq!(result.stop_reason, DeepRecallStopReason::WallTime);
        assert_eq!(result.generation_ids, vec!["gen-pending"]);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
        assert!(dropped.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn early_failure_settlement_uses_only_the_reserved_window() {
        let (workspace, _, _) = workspace();
        let provider = OpenRouterDeepRecall::with_transport(
            config().with_test_timing(200, 20),
            Arc::new(PendingSettlementTransport),
        );
        let started = std::time::Instant::now();
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert!(started.elapsed() < Duration::from_millis(100));
        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::ProviderError);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
    }

    #[tokio::test]
    async fn explicit_pre_stream_429_retries_but_ambiguous_transport_never_does() {
        let (workspace, _, _) = workspace();
        let retryable = TransportResponse {
            status: 429,
            generation_id: None,
            retry_after: Some(Duration::ZERO),
            content_type: Some("application/json".into()),
            body: Box::pin(futures::stream::empty()),
        };
        let finished = sse_response(
            "gen-after-retry",
            vec![json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"finish","arguments":"{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"no supporting source found\"}"}}]}}],
                "usage":{"prompt_tokens":1,"completion_tokens":1,"cost":0.000001}
            })],
        );
        let scripted = Arc::new(ScriptTransport::new(vec![retryable, finished]));
        let provider =
            OpenRouterDeepRecall::with_transport(config().with_test_retry(0), scripted.clone());
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace: workspace.clone(),
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Completed);
        assert_eq!(scripted.requests.lock().unwrap().len(), 2);

        let ambiguous = Arc::new(CountingErrorTransport(AtomicUsize::new(0)));
        let provider = OpenRouterDeepRecall::with_transport(config(), ambiguous.clone());
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::ProviderError);
        assert_eq!(ambiguous.0.load(Ordering::SeqCst), 1);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
    }

    #[tokio::test]
    async fn simultaneous_preflight_caps_prefer_spend_before_context_without_dispatch() {
        let (workspace, _, _) = workspace();
        let transport = Arc::new(CountingErrorTransport(AtomicUsize::new(0)));
        let provider = OpenRouterDeepRecall::with_transport(
            config().with_test_caps(24, 1, 1),
            transport.clone(),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Capped);
        assert_eq!(result.stop_reason, DeepRecallStopReason::Spend);
        assert_eq!(transport.0.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn iteration_cap_returns_ordered_checkpoint_without_an_extra_request() {
        let (workspace, a, _) = workspace();
        let checkpoint = sse_response(
            "gen-checkpoint",
            vec![json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"record_evidence","arguments":format!("{{\"source_ids\":[\"{a}\"]}}")}}]}}],
                "usage":{"prompt_tokens":1,"completion_tokens":1,"cost":0.000001}
            })],
        );
        let scripted = Arc::new(ScriptTransport::new(vec![checkpoint]));
        let provider = OpenRouterDeepRecall::with_transport(
            config().with_test_caps(1, 96_000, 300_000),
            scripted.clone(),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Capped);
        assert_eq!(result.stop_reason, DeepRecallStopReason::ToolIterations);
        assert_eq!(result.source_ids, vec![a]);
        assert_eq!(scripted.requests.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn paid_failure_generation_lookup_replaces_reservation_with_exact_usage() {
        let (workspace, _, _) = workspace();
        let provider =
            OpenRouterDeepRecall::with_transport(config(), Arc::new(SettledFailureTransport));
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::ProviderError);
        assert_eq!(result.usage.context_tokens, 77);
        assert_eq!(result.usage.spend_micros, 88);
        assert_eq!(result.usage.unsettled_context_tokens_upper_bound, 0);
        assert_eq!(result.usage.unsettled_spend_micros_upper_bound, 0);
    }

    #[tokio::test]
    async fn second_invalid_tool_response_is_terminal_invalid_output() {
        let (workspace, _, _) = workspace();
        let invalid = |id: &str, call: &str| {
            sse_response(
                id,
                vec![json!({
                    "model":"anthropic/claude-sonnet-5","provider":"Azure",
                    "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":call,"function":{"name":"shell","arguments":"{}"}}]}}],
                    "usage":{"prompt_tokens":1,"completion_tokens":1,"cost":0.000001}
                })],
            )
        };
        let provider = OpenRouterDeepRecall::with_transport(
            config(),
            Arc::new(ScriptTransport::new(vec![
                invalid("gen-invalid-1", "call-1"),
                invalid("gen-invalid-2", "call-2"),
            ])),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::InvalidOutput);
        assert_eq!(result.usage.tool_iterations, 2);
        assert_eq!(
            result.generation_ids,
            vec!["gen-invalid-1", "gen-invalid-2"]
        );
    }

    #[tokio::test]
    async fn missing_final_usage_is_partial_with_unsettled_upper_bound() {
        let (workspace, _, _) = workspace();
        let response = sse_response(
            "gen-missing-usage",
            vec![json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"finish","arguments":"{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"no supporting source found\"}"}}]}}]
            })],
        );
        let provider = OpenRouterDeepRecall::with_transport(
            config(),
            Arc::new(ScriptTransport::new(vec![response])),
        );
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        assert_eq!(result.status, DeepRecallStatus::Partial);
        assert_eq!(result.stop_reason, DeepRecallStopReason::InvalidOutput);
        assert!(result.usage.unsettled_context_tokens_upper_bound > 0);
        assert!(result.usage.unsettled_spend_micros_upper_bound > 0);
    }

    #[test]
    fn config_hash_binds_transport_and_retry_boundaries() {
        let left = OpenRouterDeepRecall::with_transport(config(), Arc::new(PanicTransport));
        let right = OpenRouterDeepRecall::with_transport(
            config()
                .with_openrouter_base_url("https://example.invalid/api/v1")
                .unwrap(),
            Arc::new(PanicTransport),
        );
        assert_ne!(left.identity.config_hash, right.identity.config_hash);
    }

    #[test]
    fn campaign_candidate_config_hashes_are_cross_language_frozen() {
        for (model, response_model, input_price, output_price, expected) in [
            (
                "anthropic/claude-sonnet-5-20260630",
                "anthropic/claude-sonnet-5",
                2_000_000,
                10_000_000,
                "a0163962e23e5f34bd1d48e82d149b88b59f0f224f7cd171a92853bde455aedb",
            ),
            (
                "openai/gpt-5.6-luna-20260709",
                "openai/gpt-5.6-luna",
                1_100_000,
                6_600_000,
                "68fcd10ff271213553f1989a23ba9507b2625a2c87c047511e530323c65e9efb",
            ),
            (
                "openai/gpt-5.6-sol-20260709",
                "openai/gpt-5.6-sol",
                5_500_000,
                33_000_000,
                "274585e56ecbd4e88845181c66498895e3826dd31335ef04564596f21e192f07",
            ),
        ] {
            let candidate = DeepConfig::new(
                "secret".into(),
                model.into(),
                response_model.into(),
                "campaign prompt".into(),
                vec!["azure".into()],
                input_price,
                output_price,
            )
            .unwrap();
            let provider =
                OpenRouterDeepRecall::with_transport(candidate, Arc::new(PanicTransport));
            assert_eq!(provider.identity.config_hash, expected);
        }
    }

    #[test]
    fn deep_mode_env_grammar_is_strict() {
        assert!(!deep_mode_from_value(None).unwrap());
        assert!(!deep_mode_from_value(Some("off")).unwrap());
        assert!(deep_mode_from_value(Some("on")).unwrap());
        for invalid in ["", "ON", "true", "1", " on "] {
            assert!(deep_mode_from_value(Some(invalid)).is_err(), "{invalid:?}");
        }
    }

    #[tokio::test]
    async fn reqwest_transport_never_follows_cross_origin_redirects() {
        let target_listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let target_address = target_listener.local_addr().unwrap();
        let (target_calls, target_server) = bounded_http_server(
            target_listener,
            Some(
                "HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into(),
            ),
        );
        let redirect_listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let redirect_address = redirect_listener.local_addr().unwrap();
        let redirect = format!(
            "HTTP/1.1 307 Temporary Redirect\r\nLocation: http://{target_address}/capture\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        );
        let (redirect_calls, redirect_server) =
            bounded_http_server(redirect_listener, Some(redirect));
        let config = config()
            .with_openrouter_base_url(&format!("http://{redirect_address}/api/v1"))
            .unwrap();
        let transport = ReqwestTransport::new(&config).unwrap();

        let response = transport
            .post(&json!({"sensitive": "authorized source body"}))
            .await
            .unwrap();
        assert_eq!(response.status, 307);
        redirect_server.join().unwrap();
        target_server.join().unwrap();
        assert_eq!(redirect_calls.load(Ordering::SeqCst), 1);
        assert_eq!(target_calls.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn reqwest_transport_ignores_ambient_proxy_configuration() {
        const CHILD_ORIGIN: &str = "MEMPHANT_DEEP_PROXY_TEST_CHILD_ORIGIN";
        if let Ok(origin) = std::env::var(CHILD_ORIGIN) {
            let config = config().with_openrouter_base_url(&origin).unwrap();
            let transport = ReqwestTransport::new(&config).unwrap();
            let response = transport
                .post(&json!({"sensitive": "authorized source body"}))
                .await
                .unwrap();
            assert_eq!(response.status, 400);
            return;
        }

        let origin_listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin_address = origin_listener.local_addr().unwrap();
        let (origin_calls, origin_server) = bounded_http_server(
            origin_listener,
            Some(
                "HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into(),
            ),
        );
        let proxy_listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let proxy_address = proxy_listener.local_addr().unwrap();
        let (proxy_calls, proxy_server) = bounded_http_server(
            proxy_listener,
            Some(
                "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into(),
            ),
        );
        let proxy_url = format!("http://{proxy_address}");
        let variables = [
            ("HTTP_PROXY", proxy_url.clone()),
            ("HTTPS_PROXY", proxy_url.clone()),
            ("ALL_PROXY", proxy_url.clone()),
            ("http_proxy", proxy_url.clone()),
            ("https_proxy", proxy_url.clone()),
            ("all_proxy", proxy_url),
            ("NO_PROXY", String::new()),
            ("no_proxy", String::new()),
        ];
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "deep_recall_openrouter::tests::reqwest_transport_ignores_ambient_proxy_configuration",
                "--nocapture",
            ])
            .env(
                CHILD_ORIGIN,
                format!("http://{origin_address}/api/v1"),
            )
            .envs(variables)
            .output()
            .unwrap();
        origin_server.join().unwrap();
        proxy_server.join().unwrap();
        assert!(
            output.status.success(),
            "proxy child failed:\nstdout: {}\nstderr: {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(origin_calls.load(Ordering::SeqCst), 1);
        assert_eq!(proxy_calls.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn reqwest_transport_does_not_replay_after_connection_drop() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (calls, server) = bounded_http_server(listener, None);
        let config = config()
            .with_openrouter_base_url(&format!("http://{address}/api/v1"))
            .unwrap();
        let transport = ReqwestTransport::new(&config).unwrap();

        assert!(transport.post(&json!({"request": "once"})).await.is_err());
        server.join().unwrap();
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn reqwest_transport_streams_against_a_scripted_openrouter_server() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut buffer = [0u8; 4096];
            loop {
                let read = socket.read(&mut buffer).unwrap();
                request.extend_from_slice(&buffer[..read]);
                let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers = String::from_utf8_lossy(&request[..header_end + 4]);
                let length = headers
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length:")
                            .map(str::trim)
                            .and_then(|value| value.parse::<usize>().ok())
                    })
                    .unwrap();
                if request.len() >= header_end + 4 + length {
                    break;
                }
            }
            let header_end = request
                .windows(4)
                .position(|window| window == b"\r\n\r\n")
                .unwrap();
            let body: Value = serde_json::from_slice(&request[header_end + 4..]).unwrap();
            assert_eq!(body["provider"]["only"], json!(["azure"]));
            assert_eq!(body["max_completion_tokens"], 4096);
            let event = json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"finish","arguments":"{\"source_ids\":[],\"evidence_status\":\"insufficient\",\"reason\":\"no supporting source found\"}"}}]}}],
                "usage":{"prompt_tokens":1,"completion_tokens":1,"cost":0.000001}
            });
            let response_body = format!("data: {event}\n\ndata: [DONE]\n\n");
            write!(
                socket,
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nX-Generation-Id: gen-http\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                response_body.len(),
                response_body
            )
            .unwrap();
        });
        let (workspace, _, _) = workspace();
        let provider = OpenRouterDeepRecall::new(
            config()
                .with_openrouter_base_url(&format!("http://{address}/api/v1"))
                .unwrap(),
        )
        .unwrap();
        let result = provider
            .gather(DeepRecallProviderRequest {
                query: "q".into(),
                workspace,
            })
            .await
            .unwrap();
        server.join().unwrap();
        assert_eq!(result.status, DeepRecallStatus::Completed);
        assert_eq!(result.generation_ids, vec!["gen-http"]);
    }
}
