use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use memphant_core::{
    StructuredObservation, StructuredObservationDisposition, StructuredStateProvider,
    StructuredStateProviderError, StructuredStateProviderIdentity, StructuredStateRequest,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use ureq::Agent;

const URL: &str = "https://openrouter.ai/api/v1/chat/completions";
const GENERATION_URL: &str = "https://openrouter.ai/api/v1/generation";
const DEFAULT_MODEL: &str = "openai/gpt-5.6-luna-pro";
const FLASH_MODEL: &str = "google/gemini-3.5-flash";
const FLASH_PROVIDER: &str = "google-ai-studio";
const DEEPSEEK_MODEL: &str = "deepseek/deepseek-v4-flash";
const DEEPSEEK_PROVIDERS: [&str; 2] = ["deepinfra", "wandb"];
const CONTRACT_REVISION: &str = "structured-observation.v1";
const MAX_ATTEMPTS: usize = 3;
const MAX_OUTPUT_TOKENS: u64 = 4096;
const MAX_REQUEST_BYTES: usize = 131_072;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const GLOBAL_TIMEOUT: Duration = Duration::from_secs(240);
const RESPONSE_LIMIT: u64 = 4 * 1024 * 1024;
const PROMPT_PATH_ENV: &str = "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH";
const LEDGER_ENV: &str = "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER";
const INPUT_PRICE_ENV: &str = "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION";
const OUTPUT_PRICE_ENV: &str = "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StructuredStateRequestPlan {
    pub serialized_request: Vec<u8>,
    pub request_sha256: String,
    pub extraction_key: String,
    pub per_attempt_reservation_nanos: u64,
    pub maximum_reservation_nanos: u64,
    pub maximum_attempts: usize,
}

pub fn plan_structured_state_request(
    request: &StructuredStateRequest,
    model: &str,
    prompt: &str,
    reasoning_effort: Option<&str>,
    input_price_nanos_per_million: u64,
    output_price_nanos_per_million: u64,
) -> Result<StructuredStateRequestPlan, StructuredStateProviderError> {
    if model.trim().is_empty() || prompt.trim().is_empty() {
        return Err(invalid(
            "structured-state model and prompt must not be empty",
        ));
    }
    if input_price_nanos_per_million == 0 || output_price_nanos_per_million == 0 {
        return Err(invalid("structured-state price ceilings must be positive"));
    }
    let schema = response_schema();
    let provider_policy = provider_preferences(
        model,
        input_price_nanos_per_million,
        output_price_nanos_per_million,
    );
    let payload = serde_json::to_string(request)
        .map_err(|error| invalid(format!("structured-state request payload: {error}")))?;
    let mut body = json!({
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": payload}
        ],
        "seed": 0,
        "stream": false,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "memphant_structured_observations",
                "strict": true,
                "schema": schema
            }
        },
        "provider": provider_policy,
    });
    if model == FLASH_MODEL {
        body["temperature"] = json!(0);
    }
    if let Some(effort) = reasoning_effort {
        body["reasoning"] = json!({"effort": effort});
    }
    let serialized_request = serde_json::to_vec(&body)
        .map_err(|error| invalid(format!("structured-state request: {error}")))?;
    if serialized_request.len() >= MAX_REQUEST_BYTES {
        return Err(invalid(format!(
            "request reaches or exceeds {MAX_REQUEST_BYTES}-byte limit"
        )));
    }

    let request_sha256 = sha256(&serialized_request);
    let schema_sha256 = sha256(
        serde_json::to_vec(&response_schema())
            .expect("static structured-state schema serializes")
            .as_slice(),
    );
    let extraction_key = sha256(
        serde_json::to_vec(&json!({
            "contract_revision": CONTRACT_REVISION,
            "source_kind": request.source_kind,
            "source_body_sha256": request.source_body_sha256,
            "batch_index": request.batch_index,
            "evidence_slices": request.evidence_slices,
            "requested_model": model,
            "provider_policy": provider_preferences(
                model,
                input_price_nanos_per_million,
                output_price_nanos_per_million,
            ),
            "prompt_sha256": sha256(prompt.as_bytes()),
            "schema_sha256": schema_sha256,
            "seed": 0,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_request_bytes": MAX_REQUEST_BYTES,
            "maximum_attempts": MAX_ATTEMPTS,
            "reasoning_effort": reasoning_effort,
        }))
        .expect("structured-state extraction identity serializes")
        .as_slice(),
    );
    // A byte can be at most one tokenizer token, so this stays conservative
    // without importing a model-specific tokenizer into the pure census path.
    let input = reserve_nanos(
        serialized_request.len() as u64,
        input_price_nanos_per_million,
    )?;
    let output = reserve_nanos(MAX_OUTPUT_TOKENS, output_price_nanos_per_million)?;
    let per_attempt_reservation_nanos = input
        .checked_add(output)
        .ok_or_else(|| invalid("structured-state reservation overflow"))?;
    let maximum_reservation_nanos = per_attempt_reservation_nanos
        .checked_mul(MAX_ATTEMPTS as u64)
        .ok_or_else(|| invalid("structured-state retry reservation overflow"))?;
    Ok(StructuredStateRequestPlan {
        serialized_request,
        request_sha256,
        extraction_key,
        per_attempt_reservation_nanos,
        maximum_reservation_nanos,
        maximum_attempts: MAX_ATTEMPTS,
    })
}

fn reserve_nanos(tokens: u64, nanos_per_million: u64) -> Result<u64, StructuredStateProviderError> {
    tokens
        .checked_mul(nanos_per_million)
        .and_then(|value| value.checked_add(999_999))
        .map(|value| value / 1_000_000)
        .ok_or_else(|| invalid("structured-state reservation overflow"))
}

pub(crate) fn provider_from_env() -> Result<Option<Arc<dyn StructuredStateProvider>>, String> {
    if std::env::var("MEMPHANT_STRUCTURED_STATE").as_deref() != Ok("on") {
        return Ok(None);
    }
    let key = required_env("OPENROUTER_API_KEY")?;
    let model = std::env::var("MEMPHANT_STRUCTURED_STATE_MODEL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_MODEL.to_string());
    let prompt_path = std::env::var_os(PROMPT_PATH_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| format!("{PROMPT_PATH_ENV} is required"))?;
    let prompt = load_prompt(&prompt_path)?;
    let input_price = parse_positive_u64_env(INPUT_PRICE_ENV)?;
    let output_price = parse_positive_u64_env(OUTPUT_PRICE_ENV)?;
    let ledger = std::env::var_os(LEDGER_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    let mut provider = OpenRouterStructuredState::new(
        model,
        prompt,
        input_price,
        output_price,
        Arc::new(UreqTransport::new(key)),
        Duration::from_millis(500),
        ledger,
    );
    if let Some(effort) = std::env::var("MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT")
        .ok()
        .filter(|value| !value.trim().is_empty())
    {
        if !matches!(effort.as_str(), "minimal" | "low" | "medium" | "high") {
            return Err(
                "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT must be minimal, low, medium, or high"
                    .to_string(),
            );
        }
        provider = provider.with_reasoning_effort(effort);
    }
    Ok(Some(Arc::new(provider)))
}

fn required_env(name: &str) -> Result<String, String> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("{name} is required"))
}

fn parse_positive_u64_env(name: &str) -> Result<u64, String> {
    required_env(name)?
        .parse::<u64>()
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("{name} must be a positive integer"))
}

fn load_prompt(path: &Path) -> Result<String, String> {
    let prompt = fs::read_to_string(path).map_err(|error| {
        format!(
            "failed to read {PROMPT_PATH_ENV}={}: {error}",
            path.display()
        )
    })?;
    let prompt = prompt
        .strip_suffix("\r\n")
        .or_else(|| prompt.strip_suffix('\n'))
        .unwrap_or(&prompt)
        .to_string();
    if prompt.trim().is_empty() {
        return Err(format!("{PROMPT_PATH_ENV} must not be empty"));
    }
    Ok(prompt)
}

#[derive(Clone)]
struct OpenRouterStructuredState {
    model: String,
    prompt: String,
    input_price_nanos_per_million: u64,
    output_price_nanos_per_million: u64,
    identity: StructuredStateProviderIdentity,
    transport: Arc<dyn Transport>,
    ledger: Option<PathBuf>,
    ledger_lock: Arc<Mutex<()>>,
    retry_base: Duration,
    reasoning_effort: Option<String>,
}

impl OpenRouterStructuredState {
    fn new(
        model: String,
        prompt: String,
        input_price_nanos_per_million: u64,
        output_price_nanos_per_million: u64,
        transport: Arc<dyn Transport>,
        retry_base: Duration,
        ledger: Option<PathBuf>,
    ) -> Self {
        Self {
            identity: StructuredStateProviderIdentity {
                model: compiler_model_identity(&model, None),
                prompt_hash: sha256(prompt.as_bytes()),
                schema_hash: sha256(
                    serde_json::to_vec(&response_schema())
                        .expect("static structured-state schema serializes")
                        .as_slice(),
                ),
            },
            model,
            prompt,
            input_price_nanos_per_million,
            output_price_nanos_per_million,
            transport,
            ledger,
            ledger_lock: Arc::new(Mutex::new(())),
            retry_base,
            reasoning_effort: None,
        }
    }

    fn with_reasoning_effort(mut self, effort: String) -> Self {
        self.identity.model = compiler_model_identity(&self.model, Some(&effort));
        self.reasoning_effort = Some(effort);
        self
    }

    fn plan(
        &self,
        request: &StructuredStateRequest,
    ) -> Result<StructuredStateRequestPlan, StructuredStateProviderError> {
        plan_structured_state_request(
            request,
            &self.model,
            &self.prompt,
            self.reasoning_effort.as_deref(),
            self.input_price_nanos_per_million,
            self.output_price_nanos_per_million,
        )
    }

    fn extract_sync(
        &self,
        request: &StructuredStateRequest,
    ) -> Result<Vec<StructuredObservation>, StructuredStateProviderError> {
        let plan = self.plan(request)?;
        for attempt in 1..=plan.maximum_attempts {
            let attempt_id = uuid::Uuid::new_v4().to_string();
            let started = Instant::now();
            self.record_attempt(&AttemptEvent::started(
                &attempt_id,
                request,
                &plan,
                &self.model,
                attempt,
            ))?;
            let response = match self.transport.post(&plan.serialized_request) {
                Ok(response) => response,
                Err(_) => {
                    self.record_attempt(&AttemptEvent::failed(
                        &attempt_id,
                        request,
                        &plan,
                        &self.model,
                        attempt,
                        "transport_error",
                        started.elapsed(),
                    ))?;
                    return Err(StructuredStateProviderError::Unavailable(
                        "OpenRouter transport failed; completion was not resent".to_string(),
                    ));
                }
            };
            if !(200..300).contains(&response.status) {
                self.record_attempt(&AttemptEvent::http_error(
                    &attempt_id,
                    request,
                    &plan,
                    &self.model,
                    attempt,
                    &response,
                    started.elapsed(),
                ))?;
                if is_retryable_status(response.status) && attempt < plan.maximum_attempts {
                    std::thread::sleep(
                        response
                            .retry_after
                            .unwrap_or_else(|| self.retry_base.saturating_mul(1 << (attempt - 1))),
                    );
                    continue;
                }
                return Err(StructuredStateProviderError::Unavailable(format!(
                    "OpenRouter HTTP {}: {}",
                    response.status,
                    openrouter_error_message(&response.body)
                )));
            }
            let reconciled = match reconcile_generation(self.transport.as_ref(), &response) {
                Ok(reconciled) => reconciled,
                Err(error) => {
                    self.record_attempt(&AttemptEvent::failed(
                        &attempt_id,
                        request,
                        &plan,
                        &self.model,
                        attempt,
                        "generation_stats_lookup_failed",
                        started.elapsed(),
                    ))?;
                    return Err(StructuredStateProviderError::Unavailable(error));
                }
            };
            let observations = match decode_response(reconciled.body.clone(), request) {
                Ok(observations) => observations,
                Err(error) => {
                    self.record_attempt(&AttemptEvent::failed(
                        &attempt_id,
                        request,
                        &plan,
                        &self.model,
                        attempt,
                        "response_decode_error",
                        started.elapsed(),
                    ))?;
                    return Err(error);
                }
            };
            self.record_attempt(&AttemptEvent::completed(
                &attempt_id,
                request,
                &plan,
                &self.model,
                attempt,
                &reconciled,
                observations.len(),
                started.elapsed(),
            ))?;
            return Ok(observations);
        }
        unreachable!("bounded structured-state retry loop always returns")
    }

    fn record_attempt(&self, event: &AttemptEvent) -> Result<(), StructuredStateProviderError> {
        let Some(path) = &self.ledger else {
            return Ok(());
        };
        let _guard = self.ledger_lock.lock().map_err(|_| {
            StructuredStateProviderError::Unavailable(
                "structured-state attempt ledger lock poisoned".to_string(),
            )
        })?;
        append_json_line(path, event).map_err(|error| {
            StructuredStateProviderError::Unavailable(format!(
                "structured-state attempt ledger write failed: {error}"
            ))
        })
    }
}

impl StructuredStateProvider for OpenRouterStructuredState {
    fn identity(&self) -> &StructuredStateProviderIdentity {
        &self.identity
    }

    fn extract<'a>(
        &'a self,
        request: &'a StructuredStateRequest,
    ) -> std::pin::Pin<
        Box<
            dyn std::future::Future<
                    Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>,
                > + Send
                + 'a,
        >,
    > {
        let provider = self.clone();
        let request = request.clone();
        Box::pin(async move {
            tokio::task::spawn_blocking(move || provider.extract_sync(&request))
                .await
                .map_err(|error| {
                    StructuredStateProviderError::Unavailable(format!(
                        "OpenRouter blocking task failed: {error}"
                    ))
                })?
        })
    }
}

fn provider_preferences(model: &str, input_price: u64, output_price: u64) -> Value {
    let mut preferences = if model == FLASH_MODEL {
        json!({
            "require_parameters": true,
            "only": [FLASH_PROVIDER],
            "allow_fallbacks": false,
        })
    } else if model == DEEPSEEK_MODEL {
        json!({
            "require_parameters": true,
            "order": DEEPSEEK_PROVIDERS,
            "only": DEEPSEEK_PROVIDERS,
            "allow_fallbacks": false,
        })
    } else {
        json!({"require_parameters": true, "allow_fallbacks": false})
    };
    preferences["max_price"] = json!({
        "prompt": input_price as f64 / 1_000_000_000.0,
        "completion": output_price as f64 / 1_000_000_000.0,
    });
    preferences
}

fn compiler_model_identity(model: &str, reasoning_effort: Option<&str>) -> String {
    let mut identity = model.to_string();
    if model == FLASH_MODEL {
        identity.push_str(";provider=google-ai-studio");
    } else if model == DEEPSEEK_MODEL {
        identity.push_str(";providers=deepinfra,wandb");
    }
    identity.push_str(";fallbacks=false;seed=0;max_tokens=4096;max_request_bytes=131072");
    if model == FLASH_MODEL {
        identity.push_str(";temperature=0");
    }
    if let Some(effort) = reasoning_effort {
        identity.push_str(";reasoning_effort=");
        identity.push_str(effort);
    }
    identity
}

fn response_schema() -> Value {
    let nullable_timestamp = json!({
        "anyOf": [
            {"type": "string", "format": "date-time"},
            {"type": "null"}
        ]
    });
    json!({
        "type": "object",
        "additionalProperties": false,
        "required": ["observations"],
        "properties": {
            "observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": false,
                    "required": [
                        "namespace", "item_key", "fields", "disposition",
                        "evidence_slice_id", "evidence_quote", "valid_from", "valid_to"
                    ],
                    "properties": {
                        "namespace": {"type": "string", "minLength": 1},
                        "item_key": {"type": "string", "minLength": 1},
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": false,
                                "required": ["key", "value_json"],
                                "properties": {
                                    "key": {"type": "string", "minLength": 1},
                                    "value_json": {"type": "string"}
                                }
                            }
                        },
                        "disposition": {"type": "string", "enum": ["state", "event"]},
                        "evidence_slice_id": {"type": "string", "minLength": 1},
                        "evidence_quote": {"type": "string", "minLength": 1},
                        "valid_from": nullable_timestamp,
                        "valid_to": nullable_timestamp,
                    }
                }
            }
        }
    })
}

#[derive(Deserialize)]
struct ChatResponse {
    model: String,
    choices: Vec<Choice>,
}

#[derive(Deserialize)]
struct Choice {
    message: Message,
}

#[derive(Deserialize)]
struct Message {
    content: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireResponse {
    observations: Vec<WireObservation>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireObservation {
    namespace: String,
    item_key: String,
    fields: Vec<WireField>,
    disposition: StructuredObservationDisposition,
    evidence_slice_id: String,
    evidence_quote: String,
    valid_from: Option<String>,
    valid_to: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireField {
    key: String,
    value_json: String,
}

fn decode_response(
    body: Value,
    request: &StructuredStateRequest,
) -> Result<Vec<StructuredObservation>, StructuredStateProviderError> {
    let response: ChatResponse = serde_json::from_value(body).map_err(invalid)?;
    if response.model.trim().is_empty() || response.choices.len() != 1 {
        return Err(invalid(
            "structured response has invalid model or choice count",
        ));
    }
    let wire: WireResponse =
        serde_json::from_str(&response.choices[0].message.content).map_err(invalid)?;
    let slice_ids = request
        .evidence_slices
        .iter()
        .map(|slice| slice.id.as_str())
        .collect::<BTreeSet<_>>();
    wire.observations
        .into_iter()
        .map(|observation| {
            if !slice_ids.contains(observation.evidence_slice_id.as_str()) {
                return Err(invalid("observation names an unknown evidence slice"));
            }
            let mut fields = BTreeMap::new();
            for field in observation.fields {
                if field.key.is_empty() || fields.contains_key(&field.key) {
                    return Err(invalid(
                        "observation field keys must be nonempty and unique",
                    ));
                }
                let value = serde_json::from_str(&field.value_json)
                    .map_err(|error| invalid(format!("observation field JSON: {error}")))?;
                fields.insert(field.key, value);
            }
            Ok(StructuredObservation {
                namespace: observation.namespace,
                item_key: observation.item_key,
                fields,
                disposition: observation.disposition,
                evidence_slice_id: observation.evidence_slice_id,
                evidence_quote: observation.evidence_quote,
                valid_from: observation.valid_from,
                valid_to: observation.valid_to,
            })
        })
        .collect()
}

struct HttpResponse {
    status: u16,
    body: Value,
    retry_after: Option<Duration>,
}

trait Transport: Send + Sync {
    fn post(&self, body: &[u8]) -> Result<HttpResponse, String>;
    fn generation(&self, response_id: &str) -> Result<Value, String>;
}

struct UreqTransport {
    agent: Agent,
    key: String,
}

impl UreqTransport {
    fn new(key: String) -> Self {
        let config = Agent::config_builder()
            .timeout_connect(Some(CONNECT_TIMEOUT))
            .timeout_global(Some(GLOBAL_TIMEOUT))
            .http_status_as_error(false)
            .build();
        Self {
            agent: config.into(),
            key,
        }
    }
}

impl Transport for UreqTransport {
    fn post(&self, body: &[u8]) -> Result<HttpResponse, String> {
        let mut response = self
            .agent
            .post(URL)
            .header("authorization", &format!("Bearer {}", self.key))
            .header("content-type", "application/json")
            .header("http-referer", "https://github.com/memphant")
            .header("x-title", "memphant-structured-state")
            .header("x-openrouter-metadata", "enabled")
            .send(body)
            .map_err(|_| "OpenRouter transport error".to_string())?;
        let status = response.status().as_u16();
        let response_id = response
            .headers()
            .get("x-generation-id")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        let retry_after = response
            .headers()
            .get("retry-after")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.trim().parse::<u64>().ok())
            .map(Duration::from_secs);
        let mut body = response
            .body_mut()
            .with_config()
            .limit(RESPONSE_LIMIT)
            .read_json()
            .map_err(|_| "OpenRouter response decode failed".to_string())?;
        backfill_response_id(&mut body, response_id.as_deref());
        Ok(HttpResponse {
            status,
            body,
            retry_after,
        })
    }

    fn generation(&self, response_id: &str) -> Result<Value, String> {
        for (index, delay_seconds) in [1_u64, 2, 4, 8, 16, 0].into_iter().enumerate() {
            let mut response = self
                .agent
                .get(GENERATION_URL)
                .query("id", response_id)
                .header("authorization", &format!("Bearer {}", self.key))
                .header("http-referer", "https://github.com/memphant")
                .header("x-title", "memphant-structured-state")
                .header("x-openrouter-metadata", "enabled")
                .call()
                .map_err(|_| "OpenRouter generation statistics transport error".to_string())?;
            let status = response.status().as_u16();
            if (200..300).contains(&status) {
                return response
                    .body_mut()
                    .with_config()
                    .limit(RESPONSE_LIMIT)
                    .read_json()
                    .map_err(|_| "OpenRouter generation statistics decode failed".to_string());
            }
            if status != 404 || index == 5 {
                return Err("OpenRouter generation statistics HTTP error".to_string());
            }
            std::thread::sleep(Duration::from_secs(delay_seconds));
        }
        unreachable!("bounded generation lookup loop always returns")
    }
}

fn backfill_response_id(body: &mut Value, header: Option<&str>) {
    if body.get("id").and_then(Value::as_str).is_some() {
        return;
    }
    if let Some(response_id) = header.filter(|value| !value.is_empty())
        && let Some(body) = body.as_object_mut()
    {
        body.insert("id".to_string(), json!(response_id));
    }
}

fn reconcile_generation(
    transport: &dyn Transport,
    response: &HttpResponse,
) -> Result<HttpResponse, String> {
    let response_id = response
        .body
        .get("id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "OpenRouter paid response omitted its response id".to_string())?;
    let statistics = transport.generation(response_id)?;
    let data = statistics
        .get("data")
        .and_then(Value::as_object)
        .ok_or_else(|| "OpenRouter generation statistics omitted data".to_string())?;
    if data.get("id").and_then(Value::as_str) != Some(response_id) {
        return Err("OpenRouter generation statistics response id mismatch".to_string());
    }
    let model = required_string(data, "model")?;
    let provider = required_string(data, "provider_name")?;
    let prompt_tokens = required_positive_u64(data, "tokens_prompt")?;
    let completion_tokens = required_positive_u64(data, "tokens_completion")?;
    let cost = data
        .get("total_cost")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value > 0.0)
        .ok_or_else(|| "OpenRouter generation statistics omitted positive cost".to_string())?;
    let mut body = response.body.clone();
    body["model"] = json!(model);
    body["provider"] = json!(provider);
    body["usage"] = json!({
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost": cost,
    });
    Ok(HttpResponse {
        status: response.status,
        body,
        retry_after: response.retry_after,
    })
}

fn required_string<'a>(
    data: &'a serde_json::Map<String, Value>,
    key: &str,
) -> Result<&'a str, String> {
    data.get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("OpenRouter generation statistics omitted {key}"))
}

fn required_positive_u64(data: &serde_json::Map<String, Value>, key: &str) -> Result<u64, String> {
    data.get(key)
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("OpenRouter generation statistics omitted {key}"))
}

fn is_retryable_status(status: u16) -> bool {
    status == 429 || (500..600).contains(&status)
}

fn openrouter_error_message(body: &Value) -> &str {
    body.pointer("/error/metadata/raw")
        .and_then(Value::as_str)
        .or_else(|| body.pointer("/error/message").and_then(Value::as_str))
        .unwrap_or("unstructured provider error")
}

#[derive(Serialize)]
struct AttemptEvent {
    schema_version: u8,
    event: &'static str,
    attempt_id: String,
    source_kind: memphant_core::StructuredSourceKind,
    source_body_sha256: String,
    batch_index: usize,
    extraction_key: String,
    request_sha256: String,
    requested_model: String,
    attempt: usize,
    maximum_attempts: usize,
    per_attempt_reservation_nanos: u64,
    maximum_reservation_nanos: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    http_status: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    served_model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    served_provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    response_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    usage: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    observation_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    elapsed_seconds: f64,
}

impl AttemptEvent {
    fn base(
        event: &'static str,
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        elapsed: Duration,
    ) -> Self {
        Self {
            schema_version: 3,
            event,
            attempt_id: attempt_id.to_string(),
            source_kind: request.source_kind,
            source_body_sha256: request.source_body_sha256.clone(),
            batch_index: request.batch_index,
            extraction_key: plan.extraction_key.clone(),
            request_sha256: plan.request_sha256.clone(),
            requested_model: model.to_string(),
            attempt,
            maximum_attempts: plan.maximum_attempts,
            per_attempt_reservation_nanos: plan.per_attempt_reservation_nanos,
            maximum_reservation_nanos: plan.maximum_reservation_nanos,
            http_status: None,
            served_model: None,
            served_provider: None,
            response_id: None,
            usage: None,
            observation_count: None,
            error: None,
            elapsed_seconds: elapsed.as_secs_f64(),
        }
    }

    fn started(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
    ) -> Self {
        Self::base(
            "started",
            attempt_id,
            request,
            plan,
            model,
            attempt,
            Duration::ZERO,
        )
    }

    fn failed(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        error: &str,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::base("result", attempt_id, request, plan, model, attempt, elapsed);
        event.error = Some(error.to_string());
        event
    }

    fn http_error(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        response: &HttpResponse,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::failed(
            attempt_id,
            request,
            plan,
            model,
            attempt,
            "http_error",
            elapsed,
        );
        event.http_status = Some(response.status);
        event
    }

    #[allow(clippy::too_many_arguments)]
    fn completed(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        response: &HttpResponse,
        observation_count: usize,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::base("result", attempt_id, request, plan, model, attempt, elapsed);
        event.http_status = Some(response.status);
        event.served_model = response
            .body
            .get("model")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.served_provider = response
            .body
            .get("provider")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.response_id = response
            .body
            .get("id")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.usage = response.body.get("usage").cloned();
        event.observation_count = Some(observation_count);
        event
    }
}

fn append_json_line(path: &Path, event: &AttemptEvent) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    serde_json::to_writer(&mut file, event)?;
    file.write_all(b"\n")?;
    file.sync_all()
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn invalid(error: impl std::fmt::Display) -> StructuredStateProviderError {
    StructuredStateProviderError::InvalidOutput(error.to_string())
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

    use memphant_core::{
        StructuredSourceKind, evidence_slices_for_episode, evidence_slices_for_resource,
    };

    use super::*;

    const INPUT_PRICE: u64 = 2_000_000_000;
    const OUTPUT_PRICE: u64 = 10_000_000_000;

    #[derive(Default)]
    struct FakeTransport {
        responses: Mutex<VecDeque<Result<HttpResponse, String>>>,
        posted: Mutex<Vec<Vec<u8>>>,
    }

    impl FakeTransport {
        fn new(responses: Vec<Result<HttpResponse, String>>) -> Arc<Self> {
            Arc::new(Self {
                responses: Mutex::new(responses.into()),
                posted: Mutex::new(Vec::new()),
            })
        }
    }

    impl Transport for FakeTransport {
        fn post(&self, body: &[u8]) -> Result<HttpResponse, String> {
            self.posted.lock().unwrap().push(body.to_vec());
            self.responses.lock().unwrap().pop_front().unwrap()
        }

        fn generation(&self, response_id: &str) -> Result<Value, String> {
            Ok(json!({"data": {
                "id": response_id,
                "model": "served/model",
                "provider_name": "served-provider",
                "tokens_prompt": 10,
                "tokens_completion": 5,
                "total_cost": 0.001
            }}))
        }
    }

    fn prompt_fixture() -> String {
        fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/structured-state-v1.txt"),
        )
        .unwrap()
    }

    fn request(body: &str) -> StructuredStateRequest {
        StructuredStateRequest {
            source_kind: StructuredSourceKind::Episode,
            source_body_sha256: sha256(body.as_bytes()),
            batch_index: 0,
            evidence_slices: evidence_slices_for_episode(body).unwrap(),
        }
    }

    fn provider(transport: Arc<dyn Transport>) -> OpenRouterStructuredState {
        OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            INPUT_PRICE,
            OUTPUT_PRICE,
            transport,
            Duration::ZERO,
            None,
        )
    }

    fn response(content: Value) -> HttpResponse {
        HttpResponse {
            status: 200,
            body: json!({
                "id": "generation-1",
                "model": DEFAULT_MODEL,
                "choices": [{"message": {"content": content.to_string()}}]
            }),
            retry_after: None,
        }
    }

    fn wire_observation(slice_id: &str) -> Value {
        json!({
            "namespace": "profile",
            "item_key": "city",
            "fields": [{"key": "value", "value_json": "\"Oslo\""}],
            "disposition": "state",
            "evidence_slice_id": slice_id,
            "evidence_quote": "I live in Oslo.",
            "valid_from": null,
            "valid_to": null
        })
    }

    #[test]
    fn request_uses_only_source_neutral_strict_observation_fields() {
        let request = request("user: I live in Oslo.");
        let plan = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            &prompt_fixture(),
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap();
        let value: Value = serde_json::from_slice(&plan.serialized_request).unwrap();
        let content = value["messages"][1]["content"].as_str().unwrap();
        assert!(content.contains("evidence_slices"));
        assert!(content.contains("source_body_sha256"));
        for forbidden in [
            "episode_id",
            "resource_id",
            "active_state",
            "target_unit_ids",
        ] {
            assert!(!content.contains(forbidden));
        }
        let item = &value["response_format"]["json_schema"]["schema"]["properties"]["observations"]
            ["items"];
        assert_eq!(item["additionalProperties"], false);
        assert!(item["properties"].get("operation").is_none());
        assert!(item["properties"].get("target_unit_ids").is_none());
    }

    #[test]
    fn strict_decode_accepts_known_slice_and_rejects_unknown_or_extra_fields() {
        let request = request("user: I live in Oslo.");
        let known = wire_observation(&request.evidence_slices[0].id);
        let decoded = decode_response(
            response(json!({"observations": [known.clone()]})).body,
            &request,
        )
        .unwrap();
        assert_eq!(decoded[0].fields["value"], "Oslo");

        let mut unknown = known.clone();
        unknown["evidence_slice_id"] = json!("slice-unknown");
        assert!(
            decode_response(response(json!({"observations": [unknown]})).body, &request).is_err()
        );
        let mut extra = known;
        extra["operation"] = json!("replace");
        assert!(
            decode_response(response(json!({"observations": [extra]})).body, &request).is_err()
        );
    }

    #[test]
    fn planner_identity_drifts_for_every_extraction_input() {
        let request = request("user: I live in Oslo.");
        let plan = |request: &StructuredStateRequest, model: &str, prompt: &str| {
            plan_structured_state_request(request, model, prompt, None, INPUT_PRICE, OUTPUT_PRICE)
                .unwrap()
        };
        let base = plan(&request, DEFAULT_MODEL, "prompt-a");
        let mut next_batch = request.clone();
        next_batch.batch_index = 1;
        assert_ne!(
            base.extraction_key,
            plan(&next_batch, DEFAULT_MODEL, "prompt-a").extraction_key
        );
        assert_ne!(
            base.extraction_key,
            plan(&request, "other/model", "prompt-a").extraction_key
        );
        assert_ne!(
            base.extraction_key,
            plan(&request, DEFAULT_MODEL, "prompt-b").extraction_key
        );
        assert_eq!(base.request_sha256, sha256(&base.serialized_request));
    }

    #[test]
    fn reservation_rounds_up_and_covers_every_retry() {
        let plan = plan_structured_state_request(
            &request("user: hello"),
            DEFAULT_MODEL,
            "prompt",
            None,
            1,
            1,
        )
        .unwrap();
        assert!(plan.per_attempt_reservation_nanos > 0);
        assert_eq!(
            plan.maximum_reservation_nanos,
            plan.per_attempt_reservation_nanos * 3
        );
    }

    #[test]
    fn oversized_request_is_rejected_before_transport() {
        let body = format!("user: {}", "\\\"".repeat(MAX_REQUEST_BYTES));
        let request = request(&body);
        let transport = FakeTransport::new(vec![]);
        let error = provider(transport.clone())
            .extract_sync(&request)
            .unwrap_err();
        assert!(error.to_string().contains("131072-byte limit"));
        assert!(transport.posted.lock().unwrap().is_empty());
    }

    #[test]
    fn request_at_exactly_128_kib_is_rejected() {
        let mut request = StructuredStateRequest {
            source_kind: StructuredSourceKind::Resource,
            source_body_sha256: "0".repeat(64),
            batch_index: 0,
            evidence_slices: vec![memphant_core::EvidenceSlice {
                id: "slice-test".to_string(),
                body: String::new(),
                source_span: "0-1".to_string(),
            }],
        };
        let base = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            "prompt",
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap()
        .serialized_request
        .len();
        request.evidence_slices[0].body = "a".repeat(MAX_REQUEST_BYTES - base - 1);
        let below = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            "prompt",
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap();
        assert_eq!(below.serialized_request.len(), MAX_REQUEST_BYTES - 1);
        request.evidence_slices[0].body.push('a');
        assert!(
            plan_structured_state_request(
                &request,
                DEFAULT_MODEL,
                "prompt",
                None,
                INPUT_PRICE,
                OUTPUT_PRICE,
            )
            .unwrap_err()
            .to_string()
            .contains("131072-byte limit")
        );
    }

    #[test]
    fn dispatch_consumes_the_exact_planned_bytes() {
        let request = request("user: I live in Oslo.");
        let transport = FakeTransport::new(vec![Ok(response(json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        })))]);
        let provider = provider(transport.clone());
        let plan = provider.plan(&request).unwrap();
        provider.extract_sync(&request).unwrap();
        assert_eq!(
            transport.posted.lock().unwrap().as_slice(),
            &[plan.serialized_request]
        );
    }

    #[test]
    fn resource_request_never_exposes_its_local_id() {
        let body = "deploy notes";
        let request = StructuredStateRequest {
            source_kind: StructuredSourceKind::Resource,
            source_body_sha256: sha256(body.as_bytes()),
            batch_index: 4,
            evidence_slices: evidence_slices_for_resource(body, &[]).unwrap(),
        };
        let local_id = "00000000-0000-0000-0000-000000000007";
        let plan = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            "prompt",
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap();
        assert!(
            !String::from_utf8(plan.serialized_request)
                .unwrap()
                .contains(local_id)
        );
    }

    #[test]
    fn attempt_receipt_binds_source_key_requested_and_served_identity() {
        let body = "user: I live in Oslo.";
        let request = request(body);
        let transport = FakeTransport::new(vec![Ok(response(json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        })))]);
        let ledger = std::env::temp_dir().join(format!(
            "memphant-structured-observation-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            "prompt".to_string(),
            INPUT_PRICE,
            OUTPUT_PRICE,
            transport,
            Duration::ZERO,
            Some(ledger.clone()),
        );
        provider.extract_sync(&request).unwrap();
        let events = fs::read_to_string(&ledger)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        fs::remove_file(ledger).unwrap();
        assert_eq!(events[0]["source_kind"], "episode");
        assert_eq!(events[0]["source_body_sha256"], request.source_body_sha256);
        assert!(events[0]["extraction_key"].as_str().unwrap().len() == 64);
        assert!(events[0].get("episode_id").is_none());
        assert_eq!(events[0]["requested_model"], DEFAULT_MODEL);
        assert_eq!(events[1]["served_model"], "served/model");
        assert_eq!(events[1]["served_provider"], "served-provider");
    }
}
